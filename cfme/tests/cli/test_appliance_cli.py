import pytest

from cfme.utils.log_validator import LogValidator
from cfme.utils import version
from wait_for import wait_for


tzs = [
    ['Africa/Abidjan'],
    ['America/Argentina/Buenos_Aires'],
    ['Antarctica/Casey'],
    ['Arctic/Longyearbyen'],
    ['Asia/Aden'],
    ['Atlantic/Azores'],
    ['Australia/Adelaide'],
    ['Europe/Amsterdam'],
    ['Indian/Antananarivo'],
    ['Pacific/Apia'],
    ['UTC'],
]


@pytest.mark.uncollectif(lambda appliance: appliance.version < '5.9')
def test_appliance_console_cli_datetime(temp_appliance_preconfig_funcscope):
    """Grab fresh appliance and set time and date through appliance_console_cli and check result"""
    app = temp_appliance_preconfig_funcscope
    app.ssh_client.run_command("appliance_console_cli --datetime 2020-10-20T09:59:00")

    def date_changed():
        return app.ssh_client.run_command("date +%F-%T | grep 2020-10-20-10:00").success
    wait_for(date_changed)


@pytest.mark.uncollectif(lambda appliance: appliance.version < '5.9')
@pytest.mark.parametrize('timezone', tzs, ids=[tz[0] for tz in tzs])
def test_appliance_console_cli_timezone(timezone, temp_appliance_preconfig_modscope):
    """Set and check timezones are set correctly through appliance conosle cli"""
    app = temp_appliance_preconfig_modscope
    app.ssh_client.run_command("appliance_console_cli --timezone {}".format(timezone))
    app.appliance_console.timezone_check(timezone)


@pytest.mark.uncollectif(lambda appliance: appliance.version < '5.9')
def test_appliance_console_cli_db_maintenance_hourly(appliance_with_preset_time):
    """Test database hourly re-indexing through appliance console"""
    app = appliance_with_preset_time
    app.ssh_client.run_command("appliance_console_cli --db-hourly-maintenance")

    def maintenance_run():
        return app.ssh_client.run_command(
            "grep REINDEX /var/www/miq/vmdb/log/hourly_continuous_pg_maint_stdout.log").success

    wait_for(maintenance_run, timeout=300)


def test_appliance_console_cli_set_hostname(appliance, restore_hostname):
    hostname = 'test.example.com'
    appliance.appliance_console_cli.set_hostname(hostname)
    result = appliance.ssh_client.run_command("hostname -f")
    assert result.success
    assert result.output.strip() == hostname


def test_appliance_console_cli_internal_fetch_key(app_creds, unconfigured_appliance, appliance):
    fetch_key_ip = appliance.hostname
    unconfigured_appliance.appliance_console_cli.configure_appliance_internal_fetch_key(
        0, 'localhost', app_creds['username'], app_creds['password'], 'vmdb_production',
        unconfigured_appliance.unpartitioned_disks[0], fetch_key_ip, app_creds['sshlogin'],
        app_creds['sshpass'])
    unconfigured_appliance.wait_for_evm_service()
    unconfigured_appliance.wait_for_web_ui()


def test_appliance_console_cli_external_join(app_creds, appliance,
                                             temp_appliance_unconfig_funcscope):
    appliance_ip = appliance.hostname
    temp_appliance_unconfig_funcscope.appliance_console_cli.configure_appliance_external_join(
        appliance_ip, app_creds['username'], app_creds['password'], 'vmdb_production', appliance_ip,
        app_creds['sshlogin'], app_creds['sshpass'])
    temp_appliance_unconfig_funcscope.wait_for_evm_service()
    temp_appliance_unconfig_funcscope.wait_for_web_ui()


def test_appliance_console_cli_external_create(app_creds, dedicated_db_appliance,
                                               unconfigured_appliance_secondary):
    hostname = dedicated_db_appliance.hostname
    unconfigured_appliance_secondary.appliance_console_cli.configure_appliance_external_create(5,
        hostname, app_creds['username'], app_creds['password'], 'vmdb_production', hostname,
        app_creds['sshlogin'], app_creds['sshpass'])
    unconfigured_appliance_secondary.wait_for_evm_service()
    unconfigured_appliance_secondary.wait_for_web_ui()


@pytest.mark.parametrize('auth_type', ['sso_enabled', 'saml_enabled', 'local_login_disabled'],
    ids=['sso', 'saml', 'local_login'])
def test_appliance_console_cli_external_auth(auth_type, ipa_crud, app_creds, configured_appliance):
    evm_tail = LogValidator('/var/www/miq/vmdb/log/evm.log',
                            matched_patterns=['.*{} to true.*'.format(auth_type)],
                            hostname=configured_appliance.hostname,
                            username=app_creds['sshlogin'],
                            password=app_creds['sshpass'])
    evm_tail.fix_before_start()
    cmd_set = 'appliance_console_cli --extauth-opts="/authentication/{}=true"'.format(auth_type)
    assert configured_appliance.ssh_client.run_command(cmd_set)
    evm_tail.validate_logs()

    evm_tail = LogValidator('/var/www/miq/vmdb/log/evm.log',
                            matched_patterns=['.*{} to false.*'.format(auth_type)],
                            hostname=configured_appliance.hostname,
                            username=app_creds['sshlogin'],
                            password=app_creds['sshpass'])

    evm_tail.fix_before_start()
    cmd_unset = 'appliance_console_cli --extauth-opts="/authentication/{}=false"'.format(auth_type)
    assert configured_appliance.ssh_client.run_command(cmd_unset)
    evm_tail.validate_logs()


@pytest.fixture(scope='function')
def no_ipa_config(configured_appliance):
    """Make sure appliance doesn't have IPA configured"""
    configured_appliance.appliance_console_cli.uninstall_ipa_client()


def test_appliance_console_cli_ipa(ipa_crud, configured_appliance, no_ipa_config):
    ipa_args = ipa_crud.as_external_value()
    configured_appliance.appliance_console_cli.configure_ipa(**ipa_args)
    assert wait_for(lambda: configured_appliance.sssd.running)
    configured_appliance.appliance_console_cli.uninstall_ipa_client()
    assert wait_for(lambda: not configured_appliance.sssd.running)


@pytest.mark.uncollectif(lambda: version.current_version() < '5.9')
def test_appliance_console_cli_extend_storage(unconfigured_appliance):
    unconfigured_appliance.ssh_client.run_command('appliance_console_cli -t auto')

    def is_storage_extended():
        assert unconfigured_appliance.ssh_client.run_command("df -h | grep /var/www/miq_tmp")
    wait_for(is_storage_extended)


@pytest.mark.uncollectif(lambda: version.current_version() < '5.9')
def test_appliance_console_cli_extend_log_storage(unconfigured_appliance):
    unconfigured_appliance.ssh_client.run_command('appliance_console_cli -l auto')

    def is_storage_extended():
        assert unconfigured_appliance.ssh_client.run_command("df -h | grep /vg_miq_logs")
    wait_for(is_storage_extended)


@pytest.mark.uncollectif(lambda: version.current_version() < '5.9')
def test_appliance_console_cli_configure_dedicated_db(unconfigured_appliance, app_creds):
    unconfigured_appliance.appliance_console_cli.configure_appliance_dedicated_db(
        app_creds['username'], app_creds['password'], 'vmdb_production',
        unconfigured_appliance.unpartitioned_disks[0]
    )
    wait_for(lambda: unconfigured_appliance.db.is_dedicated_active)


@pytest.mark.uncollectif(lambda appliance: version.current_version() < '5.9.1')
def test_appliance_console_cli_ha_crud(unconfigured_appliances, app_creds):
    """Tests the configuration of HA with three appliances including failover to standby node"""
    apps = unconfigured_appliances
    app0_ip = apps[0].hostname
    app1_ip = apps[1].hostname
    # Configure primary database
    apps[0].appliance_console_cli.configure_appliance_dedicated_db(
        app_creds['username'], app_creds['password'], 'vmdb_production',
        apps[0].unpartitioned_disks[0]
    )
    wait_for(lambda: apps[0].db.is_dedicated_active)
    # Configure webui access on EVM appliance
    apps[2].appliance_console_cli.configure_appliance_external_create(1,
        app0_ip, app_creds['username'], app_creds['password'], 'vmdb_production', app0_ip,
        app_creds['sshlogin'], app_creds['sshpass'])
    apps[2].wait_for_evm_service()
    apps[2].wait_for_web_ui()
    # Configure primary node
    apps[0].appliance_console_cli.configure_appliance_dedicated_ha_primary(
        app_creds['username'], app_creds['password'], 'primary', app0_ip, '1', 'vmdb_production'
    )
    # Configure standby node
    apps[1].appliance_console_cli.configure_appliance_dedicated_ha_standby(
        app_creds['username'], app_creds['password'], 'standby', app0_ip, app1_ip, '2',
        'vmdb_production', apps[1].unpartitioned_disks[0]
    )
    # Configure automatic failover on EVM appliance
    command_set = ('ap', '', '9', '1', '')
    apps[2].appliance_console.run_commands(command_set)

    def is_ha_monitor_started(appliance):
        return bool(appliance.ssh_client.run_command(
            "grep {} /var/www/miq/vmdb/config/failover_databases.yml".format(app1_ip)).success)
    wait_for(is_ha_monitor_started, func_args=[apps[2]], timeout=300, handle_exception=True)
    # Cause failover to occur
    result = apps[0].ssh_client.run_command('systemctl stop $APPLIANCE_PG_SERVICE', timeout=15)
    assert result.success, "Failed to stop APPLIANCE_PG_SERVICE: {}".format(result.output)

    def is_failover_started(appliance):
        return bool(appliance.ssh_client.run_command(
            "grep 'Starting to execute failover' /var/www/miq/vmdb/log/ha_admin.log").success)
    wait_for(is_failover_started, func_args=[apps[2]], timeout=450, handle_exception=True)
    apps[2].wait_for_evm_service()
    apps[2].wait_for_web_ui()
