# -*- coding: utf-8 -*-
"""Plugin enabling us to isolate browser sessions per test.

If active, then when each test ends, the browser gets killed. That ensures that whatever way the
browser session could be tainted after a test, the next test should not be affected.
"""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        '--browser-isolation',
        action='store_true',
        default=False,
        help=(
            'Isolate browser sessions for each test. That makes sure that whatever state the '
            'browser is in after a test, it will be killed so the next test will have to check out '
            'a fresh browser session.'))


@pytest.mark.hookwrapper(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    yield
    if item.config.getoption("browser_isolation"):
        holder = item.config.pluginmanager.getplugin('appliance-holder')
        if holder:
            appliance = holder.held_appliance
            for implementation in [appliance.browser, appliance.ssui]:
                implementation.quit_browser()
