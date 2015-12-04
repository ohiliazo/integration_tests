"""Logging framework

This module creates the cfme logger, for use throughout the project. This logger only captures log
messages explicitly sent to it, not logs emitted by other components (such as selenium). To capture
those, consider using the pytest-capturelog plugin.

Example Usage
^^^^^^^^^^^^^

.. code-block:: python

    from utils.log import logger

    logger.debug('debug log message')
    logger.info('info log message')
    logger.warning('warning log message')
    logger.error('error log message')
    logger.critical('critical log message')

The above will result in the following output in ``cfme_tests/logs/cfme.log``::

    1970-01-01 00:00:00,000 [D] debug log message (filename.py:3)
    1970-01-01 00:00:00,000 [I] info log message (filename.py:4)
    1970-01-01 00:00:00,000 [W] warning log message (filename.py:5)
    1970-01-01 00:00:00,000 [E] error log message (filename.py:6)
    1970-01-01 00:00:00,000 [C] fatal log message (filename.py:7)

Additionally, if ``log_error_to_console`` is True (see below), the following will be
written to stderr::

    [E] error (filename.py:6)
    [C] fatal (filename.py:7)

Log Message Source
^^^^^^^^^^^^^^^^^^

We have added a custom log record attribute that can be used in log messages: ``%(source)s`` This
attribute is included in the default 'cfme' logger configuration.

This attribute will be generated by default and include the filename and line number from where the
log message was emitted. It will attempt to convert file paths to be relative to cfme_tests, but use
the absolute file path if a relative path can't be determined.

When writting generic logging facilities, it is sometimes helpful to override
those source locations to make the resultant log message more useful. To do so, pass the extra
``source_file`` (str) and ``source_lineno`` (int) to the log emission::

    logger.info('info log message', extra={'source_file': 'somefilename.py', 'source_lineno': 7})

If ``source_lineno`` is ``None`` and ``source_file`` is included, the line number will be omitted.
This is useful in cases where the line number can't be determined or isn't necessary.

Configuration
^^^^^^^^^^^^^

.. code-block:: yaml

    # in env.yaml
    logging:
        # Can be one of DEBUG, INFO, WARNING, ERROR, CRITICAL
        level: INFO
        # Maximum logfile size, in bytes, before starting a new logfile
        # Set to 0 to disable log rotation
        max_logfile_size: 0
        # Maximimum backup copies to make of rotated log files (e.g. cfme.log.1, cfme.log.2, ...)
        # Set to 0 to keep no backups
        max_logfile_backups: 0
        # If True, messages of level ERROR and CRITICAL are also written to stderr
        errors_to_console: False
        # Default file format
        file_format: "%(asctime)-15s [%(levelname).1s] %(message)s (%(source)s)"
        # Default format to console if errors_to_console is True
        stream_format: "[%(levelname)s] %(message)s (%(source)s)"

Additionally, individual logger configurations can be overridden by defining nested configuration
values using the logger name as the configuration key. Note that the name of the logger objects
exposed by this module don't obviously line up with their key in ``cfme_data``. The 'name' attribute
of loggers can be inspected to get this value::

    >>> utils.log.logger.name
    'cfme'
    >>> utils.log.perflog.logger.name
    'perf'

Here's an example of those names being used in ``env.local.yaml`` to configure loggers
individually:

.. code-block:: yaml

    logging:
        cfme:
            # set the cfme log level to debug
            level: DEBUG
        perf:
            # make the perflog a little more "to the point"
            file_format: "%(message)s"

Notes:

* The ``cfme`` and ``perf`` loggers are guaranteed to exist when using this module.
* The name of a logger is used to generate its filename, and will usually not have the word
  "log" in it.

  * ``perflog``'s logger name is ``perf`` for this reason, resulting in ``log/perf.log``
    instead of ``log/perflog.log``.
  * Similarly, ``logger``'s' name is ``cfme``, to prevent having ``log/logger.log``.

.. warning::

    Creating a logger with the same name as one of the default configuration keys,
    e.g. ``create_logger('level')`` will cause a rift in space-time (or a ValueError).

    Do not attempt.

Message Format
^^^^^^^^^^^^^^

    ``year-month-day hour:minute:second,millisecond [Level] message text (file:linenumber)``

``[Level]``:

    One letter in square brackets, where ``[I]`` corresponds to INFO, ``[D]`` corresponds to
    DEBUG, and so on.

``(file:linenumber)``:

    The relative location from which this log message was emitted. Paths outside

Members
^^^^^^^

"""
import fauxfactory
import inspect
import logging
import sys
import warnings
import datetime as dt
from logging.handlers import RotatingFileHandler, SysLogHandler
from pkgutil import iter_modules
from time import time
from traceback import extract_tb, format_tb

import psphere

from utils import conf, lazycache, safe_string
from utils.path import get_rel_path, log_path

MARKER_LEN = 80

# set logging defaults
_default_conf = {
    'level': 'INFO',
    'max_file_size': 0,
    'max_file_backups': 0,
    'errors_to_console': False,
    'file_format': '%(asctime)-15s [%(levelname).1s] %(message)s (%(source)s)',
    'stream_format': '[%(levelname)s] %(message)s (%(source)s)'
}

# let logging know we made a TRACE level
logging.TRACE = 5
logging.addLevelName(logging.TRACE, 'TRACE')


class logger_wrap(object):
    """ Sets up the logger by default, used as a decorator in utils.appliance

    If the logger doesn't exist, sets up a sensible alternative
    """
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func):
        def newfunc(*args, **kwargs):
            cb = kwargs.get('log_callback', None)
            if not cb:
                cb = logger.info
            kwargs['log_callback'] = lambda msg: cb(self.args[0].format(msg))
            return func(*args, **kwargs)
        return newfunc


class TraceLogger(logging.Logger):
    """A trace-loglevel-aware :py:class:`Logger <python:logging.Logger>`"""
    def trace(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'TRACE'.

        """
        if self.isEnabledFor(logging.TRACE):
            self._log(logging.TRACE, msg, args, **kwargs)
logging._loggerClass = TraceLogger


class TraceLoggerAdapter(logging.LoggerAdapter):
    """A trace-loglevel-aware :py:class:`LoggerAdapter <python:logging.LoggerAdapter>`"""
    def trace(self, msg, *args, **kwargs):
        """
        Delegate a trace call to the underlying logger, after adding
        contextual information from this adapter instance.
        """
        msg, kwargs = self.process(msg, kwargs)
        self.logger.trace(msg, *args, **kwargs)


class SyslogMsecFormatter(logging.Formatter):
    """ A custom Formatter for the syslogger which changes the log timestamps to
    have millisecond resolution for compatibility with splunk.
    """

    converter = dt.datetime.fromtimestamp

    # logging.Formatter impl hates pep8
    def formatTime(self, record, datefmt=None):  # NOQA
        ct = self.converter(record.created)
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            t = ct.strftime("%Y-%m-%d %H:%M:%S")
            s = "%s.%03d" % (t, record.msecs)
        return s


class NamedLoggerAdapter(TraceLoggerAdapter):
    """An adapter that injects a name into log messages"""
    def process(self, message, kwargs):
        return '(%s) %s' % (self.extra, message), kwargs


def _load_conf(logger_name=None):
    # Reload logging conf from env, then update the logging_conf
    try:
        del(conf['env'])
    except KeyError:
        # env not loaded yet
        pass

    logging_conf = _default_conf.copy()

    yaml_conf = conf.env.get('logging', {})
    # Update the defaults with values from env yaml
    logging_conf.update(yaml_conf)
    # Additionally, look in the logging conf for file-specific loggers
    if logger_name in logging_conf:
        logging_conf.update(logging_conf[logger_name])

    return logging_conf


def _get_syslog_settings():
    try:
        syslog = conf['env']['syslog']
        return (syslog['address'], int(syslog['port']))
    except KeyError:
        return None


class _RelpathFilter(logging.Filter):
    """Adds the relpath attr to records

    Not actually a filter, this was the least ridiculous way to add custom dynamic
    record attributes and reduce it all down to the ``source`` record attr.

    looks for 'source_file' and 'source_lineno' on the log record, falls back to builtin
    record attributes if they aren't found.

    """
    def filter(self, record):
        try:
            relpath = get_rel_path(record.source_file)
            lineno = record.source_lineno
        except AttributeError:
            relpath = get_rel_path(record.pathname)
            lineno = record.lineno
        if lineno:
            record.source = "%s:%d" % (relpath, lineno)
        else:
            record.source = relpath

        return True


class Perflog(object):
    """Performance logger, useful for timing arbitrary events by name

    Logged events will be written to ``log/perf.log`` by default, unless
    a different log file name is passed to the Perflog initializer.

    Usage:

        from utils.log import perflog
        perflog.start('event_name')
        # do stuff
        seconds_taken = perflog.stop('event_name')
        # seconds_taken is also written to perf.log for later analysis

    """
    tracking_events = {}

    def __init__(self, perflog_name='perf'):
        self.logger = create_logger(perflog_name)

    def start(self, event_name):
        """Start tracking the named event

        Will reset the start time if the event is already being tracked

        """
        if event_name in self.tracking_events:
            self.logger.warning('"%s" event already started, resetting start time', event_name)
        else:
            self.logger.debug('"%s" event tracking started', event_name)
        self.tracking_events[event_name] = time()

    def stop(self, event_name):
        """Stop tracking the named event

        Returns:
            A float value of the time passed since ``start`` was last called, in seconds,
            *or* ``None`` if ``start`` was never called.

        """
        if event_name in self.tracking_events:
            seconds_taken = time() - self.tracking_events.pop(event_name)
            self.logger.info('"%s" event took %f seconds', event_name, seconds_taken)
            return seconds_taken
        else:
            self.logger.error('"%s" not being tracked, call .start first', event_name)
            return None


def create_logger(logger_name, filename=None, max_file_size=None, max_backups=None):
    """Creates and returns the named logger

    If the logger already exists, it will be destroyed and recreated
    with the current config in env.yaml

    """
    # If the logger already exists, destroy it
    if logger_name in logging.root.manager.loggerDict:
        del(logging.root.manager.loggerDict[logger_name])

    # Grab the logging conf
    conf = _load_conf(logger_name)

    log_path.ensure(dir=True)
    if filename:
        log_file = filename
    else:
        log_file = str(log_path.join('%s.log' % logger_name))

    # log_file is dynamic, so we can't used logging.config.dictConfig here without creating
    # a custom RotatingFileHandler class. At some point, we should do that, and move the
    # entire logging config into env.yaml

    file_formatter = logging.Formatter(conf['file_format'])
    file_handler = RotatingFileHandler(log_file, maxBytes=max_file_size or conf['max_file_size'],
        backupCount=max_backups or conf['max_file_backups'], encoding='utf8')
    file_handler.setFormatter(file_formatter)

    logger = logging.getLogger(logger_name)
    logger.addHandler(file_handler)

    syslog_settings = _get_syslog_settings()
    if syslog_settings:
        lid = fauxfactory.gen_alphanumeric(8)
        fmt = '%(asctime)s [' + lid + '] %(message)s'
        syslog_formatter = SyslogMsecFormatter(fmt=fmt)
        syslog_handler = SysLogHandler(address=syslog_settings)
        syslog_handler.setFormatter(syslog_formatter)
        logger.addHandler(syslog_handler)
    logger.setLevel(conf['level'])
    if conf['errors_to_console']:
        stream_formatter = logging.Formatter(conf['stream_format'])
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.ERROR)
        stream_handler.setFormatter(stream_formatter)

        logger.addHandler(stream_handler)

    logger.addFilter(_RelpathFilter())
    return logger


def create_sublogger(logger_sub_name, logger_name='cfme'):
    logger = create_logger(logger_name)
    return NamedLoggerAdapter(logger, logger_sub_name)


def _showwarning(message, category, filename, lineno, file=None, line=None):
    relpath = get_rel_path(filename)
    if relpath:
        # Only show warnings from inside this project
        message = "%s from %s:%d: %s" % (category.__name__, relpath, lineno, message)
        try:
            logger.warning(message)
        except ImportError:
            # In case we have both credentials.eyaml and credentials.yaml, it gets in an import loop
            # Therefore it would raise ImportError for art_client. Let's don't bother and just spit
            # it out. This should reduce number of repeated questions down by 99%.
            print "[WARNING] {}".format(message)


def format_marker(mstring, mark="-"):
    """ Creates a marker in log files using a string and leader mark.

    This function uses the constant ``MARKER_LEN`` to determine the length of the marker,
    and then centers the message string between padding made up of ``leader_mark`` characters.

    Args:
        mstring: The message string to be placed in the marker.
        leader_mark: The marker character to use for leading and trailing.

    Returns: The formatted marker string.

    Note: If the message string is too long to fit one character of leader/trailer and
        a space, then the message is returned as is.
    """
    if len(mstring) <= MARKER_LEN - 2:
        # Pad with spaces
        mstring = ' {} '.format(mstring)
        # Format centered, surrounded the leader_mark
        format_spec = '{{:{leader_mark}^{marker_len}}}'\
            .format(leader_mark=mark, marker_len=MARKER_LEN)
        mstring = format_spec.format(mstring)
    return mstring


def _custom_excepthook(type, value, traceback):
    file, lineno, function, __ = extract_tb(traceback)[-1]
    text = ''.join(format_tb(traceback)).strip()
    logger.error('Unhandled {}'.format(type.__name__))
    logger.error(text, extra={'source_file': file, 'source_lineno': lineno})
    _original_excepthook(type, value, traceback)

if '_original_excepthook' not in globals():
    # Guard the original excepthook against reloads so we don't hook twice
    _original_excepthook = sys.excepthook


def nth_frame_info(n):
    """
    Inspect the stack to determine the filename and lineno of the code running at the "n"th frame

    Args:
        n: Number of the stack frame to inspect

    Raises IndexError if the stack doesn't contain the nth frame (the caller should know this)

    Returns a frameinfo namedtuple as described in :py:func:`inspect <python:inspect.getframeinfo>`

    """
    # Inspect the stack with 1 line of context, looking at the "n"th frame to determine
    # the filename and line number of that frame
    return inspect.getframeinfo(inspect.stack(1)[n][0])


class ArtifactorLoggerAdapter(logging.LoggerAdapter):
    """Logger Adapter that hands messages off to the artifactor before logging"""
    @lazycache
    def artifactor(self):
        from fixtures.artifactor_plugin import art_client
        return art_client

    @lazycache
    def slaveid(self):
        from fixtures.artifactor_plugin import SLAVEID
        return SLAVEID or ""

    def art_log(self, level_name, message, kwargs):
        art_log_record = {
            'level': level_name,
            'message': safe_string(message),
            'extra': kwargs.get('extra', '')
        }
        self.artifactor.fire_hook('log_message', log_record=art_log_record, slaveid=self.slaveid)

    def log(self, lvl, msg, *args, **kwargs):
        level_name = logging.getLevelName(lvl).lower()
        msg, kwargs = self.process(msg, kwargs)
        self.art_log(level_name, msg, kwargs)
        return self.logger.log(lvl, msg, *args, **kwargs)

    def trace(self, msg, *args, **kwargs):
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('trace', msg, kwargs)
        return self.logger.trace(msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('debug', msg, kwargs)
        return self.logger.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('info', msg, kwargs)
        return self.logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('warning', msg, kwargs)
        return self.logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('error', msg, kwargs)
        return self.logger.error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('critical', msg, kwargs)
        return self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        kwargs['exc_info'] = 1
        msg, kwargs = self.process(msg, kwargs)
        self.art_log('error', msg, kwargs)
        return self.logger.error(msg, *args, **kwargs)

    def process(self, msg, kwargs):
        # frames
        # 0: call to nth_frame_info
        # 1: adapter process method (this method)
        # 2: adapter logging method
        # 3: original logging call
        frameinfo = nth_frame_info(3)
        extra = kwargs.get('extra', {})
        # add extra data if needed
        if not extra.get('source_file'):
            if frameinfo.filename:
                extra['source_file'] = get_rel_path(frameinfo.filename)
                extra['source_lineno'] = frameinfo.lineno
            else:
                # calling frame didn't have a filename
                extra['source_file'] = 'unknown'
                extra['source_lineno'] = 0
        kwargs['extra'] = extra
        return msg, kwargs


cfme_logger = create_logger('cfme')

logger = ArtifactorLoggerAdapter(cfme_logger, {})

perflog = Perflog()

# Capture warnings to the cfme logger using the warnings.showwarning hook
warnings.showwarning = _showwarning
warnings.simplefilter('default')

# Register a custom excepthook to log unhandled exceptions
sys.excepthook = _custom_excepthook

# Suppress psphere's really annoying "No handler found" messages.
# module[1] is the name from a (module_loader, name, ispkg) tuple
for psphere_mod in ('psphere.%s' % module[1] for module in iter_modules(psphere.__path__)):
    # Add a logger with a NullHandler for ever psphere module
    logging.getLogger(psphere_mod).addHandler(logging.NullHandler())
