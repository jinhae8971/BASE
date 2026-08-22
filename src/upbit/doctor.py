"""Preflight checks — "will this actually run on my machine?"

Run before the first launch (and whenever something looks wrong):

    mais-upbit doctor

Each check reports OK / WARN / FAIL with a concrete next step. WARN means the
dashboard still starts (e.g. no API keys yet — paper mode does not need them);
FAIL means something must be fixed first.
"""
from __future__ import annotations

import importlib
import os
import platform
import socket
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from common.config import PROJECT_ROOT, SETTINGS_FILE, get_setting

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}

# (import name, pip name) — everything the Upbit subsystem imports at runtime.
REQUIRED_PACKAGES = [
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("yaml", "pyyaml"),
    ("httpx", "httpx"),
    ("tenacity", "tenacity"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("structlog", "structlog"),
    ("typer", "typer"),
    ("apscheduler", "apscheduler"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("jwt", "PyJWT"),
    ("cryptography", "cryptography"),
]

INSTALL_HINT = 'pip install -e ".[upbit]"'


def _display_width(text: str) -> int:
    """Terminal column count — CJK glyphs occupy two cells, ASCII one."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""
    # Does this failure make the app unusable, or is it something the user fixes
    # *in* the running dashboard? Rejected API keys are the latter — refusing to
    # start would hide the very screen where you re-enter them.
    blocking: bool = True


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(
        self,
        name: str,
        status: str,
        detail: str = "",
        hint: str = "",
        *,
        blocking: bool = True,
    ) -> None:
        self.checks.append(Check(name, status, detail, hint, blocking))

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def blocking_failures(self) -> list[Check]:
        """Failures that must stop `serve` from starting at all."""
        return [c for c in self.failures if c.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    def render(self) -> str:
        width = max((_display_width(c.name) for c in self.checks), default=10)
        lines = ["", "업비트 자동매매 — 실행 전 점검", "=" * 62]
        for c in self.checks:
            pad = " " * (width - _display_width(c.name))
            lines.append(f"  [{_MARK[c.status]}] {c.name}{pad}  {c.detail}")
            if c.hint:
                lines.append(f"      └ {c.hint}")
        lines.append("=" * 62)
        if self.blocking_failures:
            lines.append(f"  {len(self.blocking_failures)}건을 먼저 해결해야 실행할 수 있습니다.")
        elif self.failures:
            lines.append(
                f"  {len(self.failures)}건 실패 — 실행은 되지만 대시보드에서 조치가 필요합니다."
            )
        elif self.warnings:
            lines.append("  실행 가능합니다. 위 경고는 확인만 하세요.")
        else:
            lines.append("  모든 점검을 통과했습니다. 바로 실행할 수 있습니다.")
        lines.append("")
        return "\n".join(lines)


# ----------------------------------------------------------------------
def _check_python(report: Report) -> None:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # whole point of doctor is to tell someone on an older interpreter why.
    if sys.version_info < (3, 11):  # noqa: UP036
        report.add(
            "Python",
            FAIL,
            f"{version} — 3.11 이상이 필요합니다.",
            "python.org 에서 3.11 이상을 설치한 뒤 가상환경을 다시 만드세요.",
        )
    else:
        report.add("Python", OK, f"{version} ({platform.system()} {platform.machine()})")


def _check_packages(report: Report) -> None:
    missing = []
    for module, package in REQUIRED_PACKAGES:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        report.add("의존성 패키지", FAIL, f"누락: {', '.join(missing)}", INSTALL_HINT)
    else:
        report.add("의존성 패키지", OK, f"{len(REQUIRED_PACKAGES)}개 모두 설치됨")


def _check_settings(report: Report) -> None:
    if not SETTINGS_FILE.exists():
        report.add(
            "설정 파일",
            WARN,
            f"{SETTINGS_FILE} 없음 — 코드 기본값으로 동작합니다.",
            "저장소 루트에서 실행하고 있는지 확인하세요.",
        )
        return
    if get_setting("upbit") is None:
        report.add(
            "설정 파일",
            WARN,
            "settings.yaml 에 upbit 섹션이 없습니다 — 기본값 사용.",
            "최신 config/settings.yaml 로 갱신하세요.",
        )
        return
    report.add("설정 파일", OK, str(SETTINGS_FILE.relative_to(PROJECT_ROOT)))


def _check_storage(report: Report) -> None:
    from .store import default_db_path

    db_path = default_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.parent / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        report.add(
            "데이터 저장소",
            FAIL,
            f"{db_path.parent} 쓰기 불가 — {exc}",
            "디렉터리 권한을 확인하거나 다른 위치로 옮기세요.",
        )
        return

    try:
        from .store import UpbitStore

        store = UpbitStore(db_path)
        n_trades = len(store.list_trades(limit=1))
        exists = "기존 DB" if db_path.exists() and db_path.stat().st_size > 0 else "신규 생성"
        report.add("데이터 저장소", OK, f"{db_path} ({exists}, 거래기록 {n_trades}건+)")
    except Exception as exc:  # surfaced as a check failure
        report.add("데이터 저장소", FAIL, f"SQLite 초기화 실패 — {exc}")


def _check_config(report: Report) -> None:
    try:
        from .strategy import load_config

        config = load_config()
    except Exception as exc:  # surfaced as a check failure
        report.add("전략 설정", FAIL, f"로드 실패 — {exc}", "대시보드 전략 탭에서 기본값 초기화")
        return

    mode_label = "실거래 (LIVE)" if config.mode == "live" else "모의 (paper)"
    report.add(
        "거래 모드",
        WARN if config.mode == "live" else OK,
        mode_label,
        "실제 자금으로 주문이 나갑니다." if config.mode == "live" else "",
    )
    report.add(
        "스케줄",
        OK if config.schedule.enabled else WARN,
        f"선정 {config.schedule.selection_time} KST · 모니터링 {config.schedule.monitor_interval_min}분 "
        f"· 강제청산 {config.schedule.eod_exit_time} KST"
        + ("" if config.schedule.enabled else "  (비활성)"),
        "" if config.schedule.enabled else "대시보드 전략 탭에서 스케줄을 켜세요.",
    )


def _check_holdings(report: Report) -> None:
    try:
        from .holdings import list_holdings

        holdings = list_holdings()
    except Exception as exc:  # surfaced as a check failure
        report.add("장기보유 제외", FAIL, str(exc))
        return
    if not holdings:
        report.add(
            "장기보유 제외",
            WARN,
            "등록된 코인이 없습니다 — 모든 코인이 거래 대상입니다.",
            "장기보유 탭에서 손대지 않을 코인을 먼저 등록하세요.",
        )
    else:
        names = ", ".join(
            h.symbol + ("" if h.locked_quantity <= 0 else f"({h.locked_quantity:g})")
            for h in holdings
        )
        report.add("장기보유 제외", OK, names)


def _check_credentials(report: Report) -> None:
    from . import credentials as creds_mod

    status = creds_mod.status()
    if status.get("error"):
        report.add(
            "API 키",
            FAIL,
            str(status["error"]),
            "설정 탭에서 키를 다시 입력하세요.",
            blocking=False,
        )
        return
    if not status.get("configured"):
        report.add(
            "API 키",
            WARN,
            "미등록 — 모의 거래는 키 없이 동작합니다.",
            "실거래하려면 대시보드 설정 탭에서 입력하세요.",
        )
        return
    source = "환경변수" if status.get("source") == "env" else "암호화 파일"
    report.add("API 키", OK, f"등록됨 ({source}) — Access {status.get('access_key')}")


def _check_public_api(report: Report) -> None:
    try:
        import httpx

        from .client import DEFAULT_BASE_URL

        base = get_setting("upbit.base_url", DEFAULT_BASE_URL)
        resp = httpx.get(f"{base}/v1/market/all", params={"isDetails": "false"}, timeout=10)
        resp.raise_for_status()
        krw = [m for m in resp.json() if str(m.get("market", "")).startswith("KRW-")]
        report.add("업비트 공개 API", OK, f"연결됨 — KRW 마켓 {len(krw)}종")
    except Exception as exc:  # network problems are a normal finding
        report.add(
            "업비트 공개 API",
            FAIL,
            f"연결 실패 — {type(exc).__name__}: {str(exc)[:100]}",
            "인터넷 연결 / 방화벽 / 프록시 설정을 확인하세요.",
        )


def _check_private_api(report: Report) -> None:
    from . import credentials as creds_mod

    if not creds_mod.load():
        return  # already reported as a WARN by _check_credentials
    try:
        from .client import UpbitClient

        client = UpbitClient()
        accounts = client.get_accounts()
        client.close()
    except Exception as exc:  # auth problems are a normal finding
        report.add(
            "업비트 계좌 조회",
            FAIL,
            f"{type(exc).__name__}: {str(exc)[:120]}",
            "업비트 Open API 관리에서 자산조회 권한과 서버 IP 허용 등록을 확인하세요. "
            "대시보드 설정 탭에서 키를 다시 입력할 수 있습니다.",
            blocking=False,
        )
        return
    krw = next((a for a in accounts if a.get("currency") == "KRW"), {})
    report.add(
        "업비트 계좌 조회",
        OK,
        f"인증 성공 — 보유 자산 {len(accounts)}종, KRW {float(krw.get('balance') or 0):,.0f}",
    )


def dashboard_address() -> tuple[str, int]:
    """The host/port `serve` will actually bind, env overrides included."""
    host = os.environ.get("UPBIT_DASHBOARD_HOST") or get_setting("upbit.dashboard.host", "127.0.0.1")
    port = int(os.environ.get("UPBIT_DASHBOARD_PORT") or get_setting("upbit.dashboard.port", 8787))
    return host, port


def port_in_use(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        return probe.connect_ex((host, port)) == 0
    finally:
        probe.close()


def is_our_dashboard(host: str, port: int) -> bool:
    """Is the thing already listening on this port our own dashboard?"""
    try:
        import httpx

        body = httpx.get(f"http://{host}:{port}/api/health", timeout=2).json()
    except Exception:  # any failure just means "not our dashboard"
        return False
    return isinstance(body, dict) and "mode" in body and "scheduler" in body


def dashboard_already_running() -> str | None:
    """URL of an already-running instance of *our* dashboard, else ``None``.

    The launchers use this so re-running them just opens the browser instead of
    failing on a port clash.
    """
    host, port = dashboard_address()
    reachable = host if host != "0.0.0.0" else "127.0.0.1"
    if port_in_use(reachable, port) and is_our_dashboard(reachable, port):
        return f"http://{reachable}:{port}"
    return None


def _check_port(report: Report) -> None:
    host, port = dashboard_address()
    reachable = host if host != "0.0.0.0" else "127.0.0.1"
    url = f"http://{reachable}:{port}"

    if not port_in_use(reachable, port):
        report.add("대시보드 포트", OK, f"{host}:{port} 사용 가능 → {url}")
        return

    if is_our_dashboard(reachable, port):
        report.add(
            "대시보드 포트",
            WARN,
            f"대시보드가 이미 실행 중입니다 → {url}",
            "브라우저에서 위 주소를 여세요. 새로 띄우려면 기존 창에서 Ctrl+C 로 종료하세요.",
        )
        return

    report.add(
        "대시보드 포트",
        FAIL,
        f"{host}:{port} 를 다른 프로그램이 사용 중입니다.",
        "다른 포트로 실행하세요:  mais-upbit serve --port 8788   (런처는 --port 8788)",
    )


def _check_static(report: Report) -> None:
    static = Path(__file__).resolve().parents[1] / "dashboard" / "static"
    missing = [f for f in ("index.html", "app.js", "charts.js", "styles.css") if not (static / f).exists()]
    if missing:
        report.add("대시보드 파일", FAIL, f"누락: {', '.join(missing)}", "저장소를 다시 받으세요.")
    else:
        report.add("대시보드 파일", OK, str(static))


def in_container() -> bool:
    """Are we running inside a container? Binding 0.0.0.0 is normal there."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "podman"))


def _check_exposure(report: Report) -> None:
    host = os.environ.get("UPBIT_DASHBOARD_HOST") or get_setting("upbit.dashboard.host", "127.0.0.1")
    if host in ("127.0.0.1", "localhost", "::1"):
        return

    if os.environ.get("UPBIT_DASHBOARD_TOKEN"):
        report.add("외부 노출", OK, f"{host} 바인드 · 토큰 인증 활성")
        return

    if in_container():
        # Inside a container 0.0.0.0 is the only way the published port works,
        # and we cannot see the host-side mapping from in here — so this is a
        # thing to confirm, not a thing to block on.
        report.add(
            "외부 노출",
            WARN,
            f"컨테이너에서 {host} 바인드 (정상) — 호스트 게시 범위를 확인하세요.",
            'compose 의 ports 가 "127.0.0.1:8787:8787" 이면 이 PC 에서만 열립니다. '
            "LAN 에 열려면 UPBIT_DASHBOARD_TOKEN 을 설정하세요.",
        )
        return

    report.add(
        "외부 노출",
        FAIL,
        f"{host} 에 바인드하면서 인증 토큰이 없습니다.",
        "UPBIT_DASHBOARD_TOKEN 을 설정하거나 127.0.0.1 로 실행하세요.",
    )


def run(*, skip_network: bool = False) -> Report:
    """Run every check and return the report."""
    report = Report()
    _check_python(report)
    _check_packages(report)
    if report.failures:
        # Nothing below can import cleanly until the basics are fixed.
        return report

    _check_settings(report)
    _check_static(report)
    _check_storage(report)
    _check_config(report)
    _check_holdings(report)
    _check_credentials(report)
    if not skip_network:
        _check_public_api(report)
        _check_private_api(report)
    _check_port(report)
    _check_exposure(report)
    return report
