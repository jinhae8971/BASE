"""Preflight checks — the thing a user runs before the first launch."""
from __future__ import annotations

import pytest

from upbit import doctor
from upbit.doctor import FAIL, OK, WARN


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point every stateful check at tmp_path so the real data_store is untouched."""
    from upbit import credentials as creds_mod
    from upbit import store as store_mod

    db = tmp_path / "upbit.sqlite"
    monkeypatch.setattr(store_mod, "default_db_path", lambda: db)
    monkeypatch.setattr(store_mod, "_STORE", store_mod.UpbitStore(db))
    monkeypatch.setattr(creds_mod, "_credentials_path", lambda: tmp_path / "creds.enc")
    monkeypatch.setattr(creds_mod, "_master_key_path", lambda: tmp_path / ".master.key")
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    monkeypatch.delenv("UPBIT_DASHBOARD_HOST", raising=False)
    monkeypatch.delenv("UPBIT_DASHBOARD_TOKEN", raising=False)
    # Whatever happens to be listening on this machine must not decide a test's
    # outcome; the port-specific tests override these explicitly.
    monkeypatch.setattr(doctor, "port_in_use", lambda h, p: False)
    monkeypatch.setattr(doctor, "in_container", lambda: False)
    return tmp_path


def by_name(report, name):
    return next(c for c in report.checks if c.name == name)


def test_offline_run_touches_no_network(isolated, monkeypatch) -> None:
    def explode(*a, **k):
        raise AssertionError("--offline 점검은 네트워크를 호출하면 안 됩니다")

    monkeypatch.setattr(doctor, "_check_public_api", explode)
    monkeypatch.setattr(doctor, "_check_private_api", explode)

    report = doctor.run(skip_network=True)
    assert report.checks
    assert not report.failures
    assert {c.name for c in report.checks} >= {"Python", "의존성 패키지", "데이터 저장소"}


def test_core_environment_passes(isolated) -> None:
    report = doctor.run(skip_network=True)
    assert by_name(report, "Python").status == OK
    assert by_name(report, "의존성 패키지").status == OK
    assert by_name(report, "대시보드 파일").status == OK
    assert by_name(report, "데이터 저장소").status == OK
    assert by_name(report, "거래 모드").detail.startswith("모의")


def test_missing_credentials_warn_not_fail(isolated) -> None:
    """Paper mode works without keys, so this must never block a launch."""
    report = doctor.run(skip_network=True)
    check = by_name(report, "API 키")
    assert check.status == WARN
    assert not report.failures


def test_empty_holdings_warns(isolated) -> None:
    assert by_name(doctor.run(skip_network=True), "장기보유 제외").status == WARN


def test_registered_holdings_pass(isolated) -> None:
    from upbit.holdings import add_holding

    add_holding("BTC", 0, "장기")
    add_holding("SOL", 2.5)
    check = by_name(doctor.run(skip_network=True), "장기보유 제외")
    assert check.status == OK
    assert "BTC" in check.detail and "SOL(2.5)" in check.detail


def test_live_mode_is_flagged(isolated) -> None:
    from upbit.strategy import save_config

    save_config({"mode": "live"})
    check = by_name(doctor.run(skip_network=True), "거래 모드")
    assert check.status == WARN
    assert "LIVE" in check.detail


def test_unwritable_storage_fails(isolated, monkeypatch, tmp_path) -> None:
    from upbit import store as store_mod

    monkeypatch.setattr(
        store_mod, "default_db_path", lambda: tmp_path / "nope" / "upbit.sqlite"
    )

    def deny(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.mkdir", deny)
    report = doctor.run(skip_network=True)
    check = by_name(report, "데이터 저장소")
    assert check.status == FAIL
    assert report.failures


def test_external_bind_without_token_fails(isolated, monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setattr(doctor, "in_container", lambda: False)
    report = doctor.run(skip_network=True)
    check = by_name(report, "외부 노출")
    assert check.status == FAIL
    assert "UPBIT_DASHBOARD_TOKEN" in check.hint


def test_external_bind_with_token_passes(isolated, monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("UPBIT_DASHBOARD_TOKEN", "s3cret-token")
    assert by_name(doctor.run(skip_network=True), "외부 노출").status == OK


def test_container_bind_without_token_is_a_warning_not_a_failure(isolated, monkeypatch) -> None:
    """In Docker, 0.0.0.0 is the only workable bind — it must not block startup."""
    monkeypatch.setenv("UPBIT_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setattr(doctor, "in_container", lambda: True)
    report = doctor.run(skip_network=True)
    check = by_name(report, "외부 노출")
    assert check.status == WARN
    assert "127.0.0.1:8787:8787" in check.hint
    assert not report.failures


def test_container_bind_with_token_passes(isolated, monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("UPBIT_DASHBOARD_TOKEN", "tok")
    monkeypatch.setattr(doctor, "in_container", lambda: True)
    assert by_name(doctor.run(skip_network=True), "외부 노출").status == OK


def test_bare_metal_bind_without_token_still_fails(isolated, monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setattr(doctor, "in_container", lambda: False)
    assert by_name(doctor.run(skip_network=True), "외부 노출").status == FAIL


def test_in_container_detects_dockerenv(monkeypatch) -> None:
    # No `isolated` fixture here — it stubs in_container, which is the thing
    # under test.
    import pathlib as _pathlib

    real_exists = _pathlib.Path.exists
    monkeypatch.setattr(
        _pathlib.Path, "exists",
        lambda self: True if str(self) == "/.dockerenv" else real_exists(self),
    )
    assert doctor.in_container() is True


def test_localhost_bind_reports_no_exposure_check(isolated) -> None:
    names = {c.name for c in doctor.run(skip_network=True).checks}
    assert "외부 노출" not in names


def test_render_is_aligned_and_readable(isolated) -> None:
    report = doctor.run(skip_network=True)
    text = report.render()
    assert "업비트 자동매매 — 실행 전 점검" in text
    assert "[✓]" in text

    # Korean labels are double-width, so the detail column lines up by *display*
    # width, not by character index — which is exactly what a terminal renders.
    columns = set()
    for check in report.checks:
        if not check.detail:
            continue
        line = next(
            line for line in text.splitlines()
            if line.startswith("  [") and check.name in line and check.detail in line
        )
        columns.add(doctor._display_width(line[: line.index(check.detail)]))
    assert len(columns) == 1, f"detail column is ragged: {sorted(columns)}"


def test_free_port_passes(isolated, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "port_in_use", lambda h, p: False)
    check = by_name(doctor.run(skip_network=True), "대시보드 포트")
    assert check.status == OK
    assert "사용 가능" in check.detail


def test_our_own_dashboard_on_the_port_is_only_a_warning(isolated, monkeypatch) -> None:
    """Re-running the launcher while it is already up must not read as an error."""
    monkeypatch.setattr(doctor, "port_in_use", lambda h, p: True)
    monkeypatch.setattr(doctor, "is_our_dashboard", lambda h, p: True)
    check = by_name(doctor.run(skip_network=True), "대시보드 포트")
    assert check.status == WARN
    assert "이미 실행 중" in check.detail
    assert "http://" in check.detail


def test_foreign_process_on_the_port_fails(isolated, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "port_in_use", lambda h, p: True)
    monkeypatch.setattr(doctor, "is_our_dashboard", lambda h, p: False)
    report = doctor.run(skip_network=True)
    check = by_name(report, "대시보드 포트")
    assert check.status == FAIL
    assert "--port" in check.hint
    assert report.failures


def test_dashboard_already_running_reports_the_url(isolated, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "port_in_use", lambda h, p: True)
    monkeypatch.setattr(doctor, "is_our_dashboard", lambda h, p: True)
    assert doctor.dashboard_already_running() == "http://127.0.0.1:8787"


def test_dashboard_already_running_is_none_for_a_foreign_process(isolated, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "port_in_use", lambda h, p: True)
    monkeypatch.setattr(doctor, "is_our_dashboard", lambda h, p: False)
    assert doctor.dashboard_already_running() is None


def test_dashboard_address_honours_env_overrides(isolated, monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_DASHBOARD_HOST", "127.0.0.1")
    monkeypatch.setenv("UPBIT_DASHBOARD_PORT", "9911")
    assert doctor.dashboard_address() == ("127.0.0.1", 9911)


def test_is_our_dashboard_is_false_when_nothing_answers() -> None:
    # Port 1 never has our dashboard on it; the probe must fail closed, not raise.
    assert doctor.is_our_dashboard("127.0.0.1", 1) is False


def test_rejected_api_key_does_not_block_startup(isolated, monkeypatch) -> None:
    """A 401 from Upbit must still let the dashboard come up — that is where the
    user re-enters the key. Seen live: it put the container in a restart loop."""
    from upbit import credentials as creds_mod
    from upbit.client import UpbitAPIError

    creds_mod.save("ACCESSKEY1234567890", "SECRETKEY0987654321")
    monkeypatch.setattr(
        doctor, "_check_public_api", lambda report: report.add("업비트 공개 API", OK, "stub")
    )

    class Boom:
        def __init__(self, *a, **k): ...
        def get_accounts(self):
            raise UpbitAPIError("HTTP 401", status=401)
        def close(self): ...

    monkeypatch.setattr("upbit.client.UpbitClient", Boom)

    report = doctor.run(skip_network=False)
    check = by_name(report, "업비트 계좌 조회")
    assert check.status == FAIL
    assert check.blocking is False
    assert report.failures            # doctor still reports it honestly
    assert not report.blocking_failures  # ...but serve must not refuse to start
    assert "실행은 되지만" in report.render()


def test_unreadable_credentials_do_not_block_startup(isolated, monkeypatch) -> None:
    from upbit import credentials as creds_mod

    monkeypatch.setattr(
        creds_mod, "status",
        lambda: {"configured": False, "stored_on_disk": True, "error": "복호화 실패"},
    )
    report = doctor.run(skip_network=True)
    check = by_name(report, "API 키")
    assert check.status == FAIL and check.blocking is False
    assert not report.blocking_failures


def test_storage_failure_still_blocks(isolated, monkeypatch, tmp_path) -> None:
    """Contrast: something the dashboard cannot fix must keep blocking."""
    from upbit import store as store_mod

    monkeypatch.setattr(store_mod, "default_db_path", lambda: tmp_path / "x" / "u.sqlite")

    def deny(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.mkdir", deny)
    report = doctor.run(skip_network=True)
    assert report.blocking_failures
    assert by_name(report, "데이터 저장소").blocking is True


def test_public_api_failure_is_reported_not_raised(isolated, monkeypatch) -> None:
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(httpx, "get", boom)
    report = doctor.run(skip_network=False)
    check = by_name(report, "업비트 공개 API")
    assert check.status == FAIL
    assert "연결 실패" in check.detail
