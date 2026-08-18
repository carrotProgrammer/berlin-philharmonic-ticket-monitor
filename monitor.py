from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from enum import Enum
from pathlib import Path
import random
import signal
import smtplib
import ssl
import sys
import threading
import time
from typing import Any
import webbrowser

import requests
from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "state.json"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "monitor.log"


EVENTS: dict[str, dict[str, str]] = {
    "2026-08-29": {
        "key": "2026-08-29",
        "short_name": "8月29日",
        "name": "Berliner Philharmoniker: Elgar & Tchaikovsky",
        "date_time": "2026年8月29日 19:00（英国当地时间）",
        "event_url": "https://www.eif.co.uk/events/berliner-philharmoniker-elgar-tchaikovsky",
        "book_url": "https://www.eif.co.uk/book/instance/260401",
        "event_id": "2632767",
        "instance_id": "260401AVDMRCCNHPKGDMVLRVGCGDBBRTP",
        "vendor_id": "260401",
    },
    "2026-08-30": {
        "key": "2026-08-30",
        "short_name": "8月30日",
        "name": "Berliner Philharmoniker: Closing Concert",
        "date_time": "2026年8月30日 19:30（英国当地时间）",
        "event_url": "https://www.eif.co.uk/events/berliner-philharmoniker-closing-concert",
        "book_url": "https://www.eif.co.uk/book/instance/260201",
        "event_id": "2632763",
        "instance_id": "260201AVGTPVCGJSRMNPGDDVLVVTHHGTM",
        "vendor_id": "260201",
    },
}

EVENT_ORDER = ["2026-08-29", "2026-08-30"]
BACKOFF_SECONDS = (60, 120, 300, 600)
PROTECTION_MARKERS = (
    "captcha",
    "cloudflare",
    "checking your browser",
    "verify you are human",
    "are you a human",
    "access denied",
    "queue-it",
    "you are now in line",
    "too many requests",
    "rate limit",
)
ACCESS_MARKERS = (
    "access seats only",
    "access pass members only",
    "remaining seats available to access pass members only",
)
UNAVAILABLE_MARKERS = (
    "tickets unavailable",
    "sold out",
    "sales not yet open",
    "booking has not yet opened",
)


class Status(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class CheckResult:
    status: Status
    reason: str
    http_summary: str = ""
    error: str = ""
    protection: bool = False
    ordinary_available: int | None = None
    access_only_quantity: int | None = None
    source: str = "公开 JSON 接口"
    details: dict[str, Any] | None = None


class ProtectionDetected(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SchemaChanged(RuntimeError):
    pass


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaChanged(f"字段 {field} 不是整数")
    if value < 0:
        raise SchemaChanged(f"字段 {field} 为负数")
    return value


def default_event_state() -> dict[str, Any]:
    return {
        "last_status": Status.UNKNOWN.value,
        "last_checked_at": None,
        "last_notified_at": None,
        "last_available_at": None,
        "last_reason": "尚未检查",
        "alert_latched": False,
        "pause_until": None,
        "backoff_stage": 0,
        "backoff_until": None,
    }


class StateStore:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.data = self._defaults()

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "events": {key: default_event_state() for key in EVENT_ORDER},
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.data
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or not isinstance(loaded.get("events"), dict):
                raise ValueError("状态文件顶层结构无效")
            merged = self._defaults()
            merged["updated_at"] = loaded.get("updated_at")
            for key in EVENT_ORDER:
                existing = loaded["events"].get(key, {})
                if isinstance(existing, dict):
                    merged["events"][key].update(existing)
            self.data = merged
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logging.getLogger("ticket_monitor").error(
                "无法读取状态文件，将使用安全默认值：%s", exc
            )
            self.data = self._defaults()
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = iso_now()
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)


def setup_logging(debug: bool = False) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ticket_monitor")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    rotating = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    logger.addHandler(rotating)
    return logger


def availability_url(event: dict[str, str]) -> str:
    return (
        "https://www.eif.co.uk/actions/event-manager/availability/event?id="
        + event["event_id"]
    )


def lock_url(event: dict[str, str]) -> str:
    return "https://www.eif.co.uk/api/instance-lock-information/" + event["instance_id"]


def _general_sale_is_open(payload: dict[str, Any], now: datetime) -> bool:
    schedules = payload.get("onSaleSchedules")
    if not isinstance(schedules, list):
        raise SchemaChanged("缺少 onSaleSchedules 列表")
    general_dates: list[datetime] = []
    for schedule in schedules:
        if not isinstance(schedule, dict):
            continue
        if str(schedule.get("status", "")).lower() != "general":
            continue
        parsed = parse_iso(schedule.get("date"))
        if parsed:
            general_dates.append(parsed)
    if not general_dates:
        raise SchemaChanged("找不到 General On Sale 时间")
    comparison_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return comparison_now >= min(general_dates).astimezone(comparison_now.tzinfo)


def classify_payloads(
    event: dict[str, str],
    availability_payload: dict[str, Any],
    lock_payload: dict[str, Any],
    now: datetime | None = None,
) -> CheckResult:
    now = now or datetime.now().astimezone()
    try:
        availability_root = availability_payload["availability"]
        instances = availability_root["instances"]
        stats = availability_root["stats"]
        if not isinstance(instances, list) or not isinstance(stats, dict):
            raise SchemaChanged("availability.instances/stats 结构无效")
        matches = [item for item in instances if str(item.get("vendor_id")) == event["vendor_id"]]
        if len(matches) != 1:
            raise SchemaChanged("无法唯一匹配目标场次 vendor_id")
        inventory = matches[0]["availability"]
        if not isinstance(inventory, dict):
            raise SchemaChanged("场次 availability 结构无效")

        ordinary_a = safe_int(inventory.get("available"), "availability.available")
        bookable = safe_int(stats.get("instances_bookable"), "stats.instances_bookable")
        unavailable_count = safe_int(
            stats.get("instances_unavailable"), "stats.instances_unavailable"
        )

        if str(lock_payload.get("id")) != event["instance_id"]:
            raise SchemaChanged("锁座接口返回了其他场次")
        lock_status = lock_payload["status"]
        if not isinstance(lock_status, dict):
            raise SchemaChanged("锁座 status 结构无效")
        ordinary_b = safe_int(lock_status.get("available"), "lock.status.available")
        lock_info = lock_status.get("lockInformation")
        if not isinstance(lock_info, list):
            raise SchemaChanged("缺少 lockInformation 列表")

        access_items: list[str] = []
        access_quantity = 0
        for item in lock_info:
            if not isinstance(item, dict) or not isinstance(item.get("lockType"), dict):
                continue
            lock_type = item["lockType"]
            if lock_type.get("availableOnWeb") is True and lock_type.get("requiresEligibility") is True:
                quantity = safe_int(item.get("quantity"), "lockInformation.quantity")
                if quantity:
                    access_quantity += quantity
                    access_items.append(f"{lock_type.get('name', '受限席位')}×{quantity}")

        sale_open = _general_sale_is_open(availability_payload, now)
        details = {
            "availability_api_available": ordinary_a,
            "lock_api_available": ordinary_b,
            "instances_bookable": bookable,
            "instances_unavailable": unavailable_count,
            "access_only_quantity": access_quantity,
            "access_items": access_items,
            "general_sale_open": sale_open,
        }

        if not sale_open:
            return CheckResult(
                Status.UNAVAILABLE,
                "普通公众销售尚未开放",
                ordinary_available=ordinary_a,
                access_only_quantity=access_quantity,
                details=details,
            )

        if ordinary_a != ordinary_b:
            return CheckResult(
                Status.UNKNOWN,
                f"两个公开接口的普通库存不一致（{ordinary_a} 与 {ordinary_b}）",
                ordinary_available=None,
                access_only_quantity=access_quantity,
                details=details,
            )

        if ordinary_a > 0 and bookable >= 1:
            return CheckResult(
                Status.AVAILABLE,
                f"两个公开接口均显示 {ordinary_a} 张普通可售库存，且场次可公开预订",
                ordinary_available=ordinary_a,
                access_only_quantity=access_quantity,
                details=details,
            )

        if ordinary_a > 0 or bookable > 0:
            return CheckResult(
                Status.UNKNOWN,
                "普通库存与场次可预订标志不一致，拒绝判定为有票",
                ordinary_available=ordinary_a,
                access_only_quantity=access_quantity,
                details=details,
            )

        if access_quantity > 0:
            item_text = "、".join(access_items)
            return CheckResult(
                Status.UNAVAILABLE,
                f"无普通票；仅有需要资格的 Access seats（{item_text}）",
                ordinary_available=0,
                access_only_quantity=access_quantity,
                details=details,
            )

        return CheckResult(
            Status.UNAVAILABLE,
            "官网显示普通库存为 0，场次不可公开预订（Tickets unavailable）",
            ordinary_available=0,
            access_only_quantity=0,
            details=details,
        )
    except (AttributeError, KeyError, TypeError, ValueError, SchemaChanged) as exc:
        return CheckResult(
            Status.UNKNOWN,
            "公开接口结构变化，无法可靠判断",
            error=str(exc),
            details={"schema_error": str(exc)},
        )


class AvailabilityChecker:
    def __init__(self, logger: logging.Logger, debug: bool = False):
        self.logger = logger
        self.debug = debug
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "BerlinPhilTicketMonitor/1.0 (personal availability monitor)",
            }
        )

    @staticmethod
    def _detect_protection(text: str) -> str | None:
        lowered = text.lower()
        return next((marker for marker in PROTECTION_MARKERS if marker in lowered), None)

    def _fetch_json(self, url: str) -> tuple[dict[str, Any], int]:
        response = self.session.get(url, timeout=(10, 20), allow_redirects=False)
        if response.status_code in (403, 429):
            raise ProtectionDetected(f"HTTP {response.status_code}", response.status_code)
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location", "")
            marker = self._detect_protection(location)
            if marker or "queue" in location.lower():
                raise ProtectionDetected(f"检测到排队或验证跳转：{location}", response.status_code)
            raise requests.HTTPError(
                f"意外的 HTTP 重定向 {response.status_code}", response=response
            )
        marker = self._detect_protection(response.text[:5000])
        if marker:
            raise ProtectionDetected(f"页面含保护/限流标志：{marker}", response.status_code)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "json" not in content_type:
            raise SchemaChanged(f"响应不是 JSON（Content-Type={content_type}）")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SchemaChanged("响应声明为 JSON，但内容无法解析") from exc
        if not isinstance(payload, dict):
            raise SchemaChanged("JSON 顶层不是对象")
        return payload, response.status_code

    def check(self, event: dict[str, str]) -> CheckResult:
        statuses: list[str] = []
        try:
            availability, status_a = self._fetch_json(availability_url(event))
            statuses.append(f"availability={status_a}")
            locks, status_b = self._fetch_json(lock_url(event))
            statuses.append(f"locks={status_b}")
            result = classify_payloads(event, availability, locks)
            result.http_summary = ", ".join(statuses)
            if result.status is Status.UNKNOWN and result.details and result.details.get("schema_error"):
                fallback = self._playwright_fallback(event)
                fallback.http_summary = result.http_summary
                if fallback.status is not Status.UNKNOWN or fallback.protection:
                    return fallback
            return result
        except ProtectionDetected as exc:
            if exc.status_code:
                statuses.append(f"HTTP={exc.status_code}")
            return CheckResult(
                Status.UNKNOWN,
                "检测到访问保护或限流，按规则退避，不尝试绕过",
                http_summary=", ".join(statuses),
                error=str(exc),
                protection=True,
            )
        except SchemaChanged as exc:
            fallback = self._playwright_fallback(event)
            fallback.http_summary = ", ".join(statuses)
            if fallback.status is Status.UNKNOWN and not fallback.error:
                fallback.error = str(exc)
            return fallback
        except requests.RequestException as exc:
            return CheckResult(
                Status.UNKNOWN,
                "网络错误或接口暂时不可用；稍后自动重试",
                http_summary=", ".join(statuses),
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:  # 防止单场异常终止整个监控
            return CheckResult(
                Status.UNKNOWN,
                "检查时出现未预期错误；稍后自动重试",
                http_summary=", ".join(statuses),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _playwright_fallback(self, event: dict[str, str]) -> CheckResult:
        """Only used after a JSON schema change; never used to bypass 403/429."""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            return CheckResult(
                Status.UNKNOWN,
                "公开接口结构变化，且 Playwright 回退不可用",
                error=str(exc),
                source="Playwright 保守回退",
            )

        protected_responses: list[tuple[int, str]] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(locale="en-GB", timezone_id="Europe/London")
                def record_protected_response(network_response: Any) -> None:
                    is_relevant = (
                        network_response.url == event["event_url"]
                        or "actions/event-manager/availability/event" in network_response.url
                        or "/instances/" in network_response.url
                    )
                    if network_response.status in (403, 429) and is_relevant:
                        protected_responses.append(
                            (network_response.status, network_response.url)
                        )

                page.on("response", record_protected_response)
                response = page.goto(
                    event["event_url"], wait_until="domcontentloaded", timeout=45000
                )
                page.wait_for_timeout(6000)
                body = " ".join(page.locator("body").inner_text(timeout=10000).split())
                links: list[tuple[str, str]] = []
                for link in page.locator("a:visible").all():
                    try:
                        links.append(
                            (
                                " ".join(link.inner_text(timeout=500).split()).lower(),
                                link.get_attribute("href") or "",
                            )
                        )
                    except Exception:
                        continue
                browser.close()

            if response and response.status in (403, 429):
                raise ProtectionDetected(f"活动页 HTTP {response.status}", response.status)
            marker = self._detect_protection(body)
            if marker or protected_responses:
                raise ProtectionDetected(
                    f"浏览器页面检测到保护标志：{marker or protected_responses[0][0]}"
                )
            lowered = body.lower()
            if any(marker in lowered for marker in ACCESS_MARKERS):
                return CheckResult(
                    Status.UNAVAILABLE,
                    "渲染页面明确显示 ACCESS SEATS ONLY / Access Pass members only",
                    source="Playwright 保守回退",
                )
            if any(marker in lowered for marker in UNAVAILABLE_MARKERS):
                return CheckResult(
                    Status.UNAVAILABLE,
                    "渲染页面明确显示 Tickets unavailable / Sold out",
                    source="Playwright 保守回退",
                )
            has_exact_book_link = any(
                text == "book now" and href == event["book_url"] for text, href in links
            )
            if has_exact_book_link:
                return CheckResult(
                    Status.AVAILABLE,
                    "渲染页面显示目标场次的可见 BOOK NOW 官方链接",
                    source="Playwright 保守回退",
                )
            return CheckResult(
                Status.UNKNOWN,
                "渲染页面没有足够信号可靠判断",
                source="Playwright 保守回退",
            )
        except ProtectionDetected as exc:
            return CheckResult(
                Status.UNKNOWN,
                "Playwright 检测到访问保护，按规则退避，不尝试绕过",
                error=str(exc),
                protection=True,
                source="Playwright 保守回退",
            )
        except PlaywrightTimeoutError as exc:
            return CheckResult(
                Status.UNKNOWN,
                "Playwright 页面加载超时；稍后自动重试",
                error=str(exc).splitlines()[0],
                source="Playwright 保守回退",
            )
        except Exception as exc:
            return CheckResult(
                Status.UNKNOWN,
                "Playwright 回退检查失败；拒绝误判为有票",
                error=f"{type(exc).__name__}: {exc}",
                source="Playwright 保守回退",
            )


def load_mail_config(required: bool = True) -> dict[str, str]:
    load_dotenv(APP_DIR / ".env")
    config = {
        "address": os.getenv("GMAIL_ADDRESS", "").strip(),
        "password": os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip(),
        "recipient": os.getenv("RECIPIENT_EMAIL", "").strip(),
    }
    missing = [name for name, value in config.items() if not value]
    if required and missing:
        names = {
            "address": "GMAIL_ADDRESS",
            "password": "GMAIL_APP_PASSWORD",
            "recipient": "RECIPIENT_EMAIL",
        }
        raise RuntimeError(".env 缺少配置：" + ", ".join(names[item] for item in missing))
    return config


def send_email(
    mail_config: dict[str, str], subject: str, body: str, logger: logging.Logger
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_config["address"]
    message["To"] = mail_config["recipient"]
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=context) as smtp:
        smtp.login(mail_config["address"], mail_config["password"])
        smtp.send_message(message)
    logger.info("邮件发送成功 | recipient=%s | subject=%s", mail_config["recipient"], subject)


def show_desktop_notification(title: str, message: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("桌面通知仅支持 Windows")
    from winotify import Notification

    toast = Notification(
        app_id="柏林爱乐余票监控",
        title=title,
        msg=message,
        duration="long",
    )
    toast.show()


def play_alert_sound() -> None:
    if sys.platform != "win32":
        raise RuntimeError("提示音测试仅支持 Windows")
    import winsound

    for frequency, duration in ((1200, 350), (1600, 350), (2000, 600)):
        winsound.Beep(frequency, duration)


def perform_notifications(
    event: dict[str, str],
    result: CheckResult,
    mail_config: dict[str, str],
    logger: logging.Logger,
    test_mode: bool = False,
) -> dict[str, bool]:
    found_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if test_mode:
        subject = "【测试通知】柏林爱乐余票监控"
        title = "柏林爱乐余票监控：测试通知"
        body = (
            "这是一封测试邮件。\n\n"
            f"演出：{event['name']}\n"
            f"演出时间：{event['date_time']}\n"
            f"测试时间：{found_at}\n"
            f"官方购票链接：{event['book_url']}\n\n"
            "如果你同时看到了 Windows 通知、听到了提示音，并且浏览器打开了官方页面，"
            "说明四种通知渠道均已执行。"
        )
    else:
        subject = f"【发现余票】柏林爱乐 {event['short_name']}"
        title = subject
        body = (
            f"演出名称：{event['name']}\n"
            f"演出日期和时间：{event['date_time']}\n"
            f"发现余票的本地时间：{found_at}\n"
            f"当前页面状态：{result.status.value}\n"
            f"判断依据：{result.reason}\n"
            f"官方购票链接：{event['book_url']}\n\n"
            "库存可能随时变化，请立即打开链接并由你本人手动购买。\n"
            "本程序不会登录、选座、锁座、加入购物车或提交订单。"
        )

    outcomes: dict[str, bool] = {}
    actions = (
        ("email", lambda: send_email(mail_config, subject, body, logger)),
        (
            "desktop",
            lambda: show_desktop_notification(
                title, f"{event['name']}\n请立即手动查看官方购票页面。"
            ),
        ),
        ("sound", play_alert_sound),
        ("browser", lambda: _open_browser(event["book_url"])),
    )
    for channel, action in actions:
        try:
            action()
            outcomes[channel] = True
            logger.info("通知渠道执行成功 | event=%s | channel=%s", event["key"], channel)
        except Exception as exc:
            outcomes[channel] = False
            logger.error(
                "通知渠道执行失败 | event=%s | channel=%s | error=%s: %s",
                event["key"],
                channel,
                type(exc).__name__,
                exc,
            )
    return outcomes


def _open_browser(url: str) -> None:
    if not webbrowser.open(url, new=2, autoraise=True):
        raise RuntimeError("系统未确认默认浏览器已打开")


def update_alert_latch(entry: dict[str, Any], status: Status) -> bool:
    """Return True only for a new availability episode.

    UNKNOWN does not re-arm an already notified episode, preventing a temporary
    network failure from causing duplicate mail when availability remains.
    """
    if status is Status.UNAVAILABLE:
        entry["alert_latched"] = False
        return False
    if status is Status.AVAILABLE:
        if bool(entry.get("alert_latched")):
            return False
        entry["alert_latched"] = True
        return True
    return False


def console_result(event: dict[str, str], result: CheckResult, debug: bool = False) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    if result.status is Status.AVAILABLE:
        description = f"有普通票（{result.ordinary_available}）"
    elif result.status is Status.UNAVAILABLE and result.access_only_quantity:
        description = "仅 Access seats，无普通票"
    elif result.status is Status.UNAVAILABLE:
        description = "无普通票"
    else:
        description = "UNKNOWN"
    print(f"[{stamp}] {event['short_name']}：{description} — {result.reason}", flush=True)
    if debug:
        print(f"  页面：{event['event_url']}")
        print(f"  购票：{event['book_url']}")
        print(f"  来源：{result.source}")
        print(f"  HTTP：{result.http_summary or '无'}")
        if result.details:
            print("  详情：" + json.dumps(result.details, ensure_ascii=False, sort_keys=True))
        if result.error:
            print(f"  错误：{result.error}")


def log_result(
    logger: logging.Logger,
    event: dict[str, str],
    result: CheckResult,
    notified: bool,
) -> None:
    logger.info(
        "检查 | event=%s | url=%s | http=%s | result=%s | reason=%s | error=%s | notified=%s",
        event["key"],
        event["event_url"],
        result.http_summary or "N/A",
        result.status.value,
        result.reason,
        result.error or "N/A",
        notified,
    )


class MonitorApp:
    def __init__(self, debug: bool = False):
        self.logger = setup_logging(debug)
        self.debug = debug
        self.store = StateStore()
        self.state = self.store.load()
        self.checker = AvailabilityChecker(self.logger, debug)
        self.stop_event = threading.Event()
        self.mail_config = load_mail_config(required=True)

    def request_stop(self, *_args: Any) -> None:
        if not self.stop_event.is_set():
            print("\n正在安全停止并保存状态……", flush=True)
            self.logger.info("收到停止信号")
            self.stop_event.set()

    def _skip_reason(self, entry: dict[str, Any]) -> str | None:
        now = datetime.now().astimezone()
        for field, label in (
            ("pause_until", "发现余票后暂停期"),
            ("backoff_until", "访问保护退避期"),
        ):
            until = parse_iso(entry.get(field))
            if until and until > now:
                seconds = max(1, int((until - now).total_seconds()))
                return f"{label}，剩余约 {seconds} 秒"
        return None

    def process_event(self, event: dict[str, str]) -> None:
        entry = self.state["events"][event["key"]]
        skip = self._skip_reason(entry)
        if skip:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] {event['short_name']}：跳过本轮（{skip}）",
                flush=True,
            )
            return

        result = self.checker.check(event)
        checked_at = iso_now()
        entry["last_status"] = result.status.value
        entry["last_checked_at"] = checked_at
        entry["last_reason"] = result.reason
        console_result(event, result, self.debug)

        if result.protection:
            stage = min(int(entry.get("backoff_stage", 0)), len(BACKOFF_SECONDS) - 1)
            seconds = BACKOFF_SECONDS[stage]
            entry["backoff_stage"] = min(stage + 1, len(BACKOFF_SECONDS) - 1)
            entry["backoff_until"] = (
                datetime.now().astimezone() + timedelta(seconds=seconds)
            ).isoformat(timespec="seconds")
            self.logger.warning(
                "访问保护退避 | event=%s | wait_seconds=%s | reason=%s | error=%s",
                event["key"], seconds, result.reason, result.error
            )
        elif result.status is not Status.UNKNOWN:
            entry["backoff_stage"] = 0
            entry["backoff_until"] = None

        if result.status is Status.AVAILABLE:
            entry["last_available_at"] = checked_at

        should_send = update_alert_latch(entry, result.status)
        notified = False
        if should_send:
            # Save the latch first so Ctrl+C or a notification-channel failure cannot spam mail.
            self.store.save()
            outcomes = perform_notifications(
                event, result, self.mail_config, self.logger, test_mode=False
            )
            notified = any(outcomes.values())
            if outcomes.get("email"):
                entry["last_notified_at"] = iso_now()
            entry["pause_until"] = (
                datetime.now().astimezone() + timedelta(minutes=5)
            ).isoformat(timespec="seconds")
            if not all(outcomes.values()):
                failed = ", ".join(name for name, ok in outcomes.items() if not ok)
                print(f"  警告：以下通知渠道失败：{failed}；请查看日志。", flush=True)
        elif result.status is Status.UNAVAILABLE:
            entry["pause_until"] = None

        self.store.save()
        log_result(self.logger, event, result, notified)

    def wait(self, seconds: float, message: str) -> bool:
        if seconds <= 0:
            return self.stop_event.is_set()
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] {message}（约 {int(round(seconds))} 秒）",
            flush=True,
        )
        return self.stop_event.wait(seconds)

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self.request_stop)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, self.request_stop)
        self.logger.info("监控启动")
        print("柏林爱乐余票监控已启动。按 Ctrl+C 安全停止。", flush=True)
        try:
            while not self.stop_event.is_set():
                cycle_started = time.monotonic()
                self.process_event(EVENTS[EVENT_ORDER[0]])
                if self.wait(random.uniform(10, 15), "等待后检查 8月30日"):
                    break
                self.process_event(EVENTS[EVENT_ORDER[1]])
                target_cycle = random.uniform(20, 40)
                elapsed = time.monotonic() - cycle_started
                remaining = max(1.0, target_cycle - elapsed)
                if self.wait(remaining, "等待下一轮检查"):
                    break
        except KeyboardInterrupt:
            self.request_stop()
        finally:
            self.store.save()
            self.checker.session.close()
            self.logger.info("监控已安全停止")
            print("监控已停止，状态已保存。", flush=True)


def run_check_once(debug: bool) -> int:
    logger = setup_logging(debug=True)
    checker = AvailabilityChecker(logger, debug=True)
    print("仅检查一次：不会发送通知、播放声音或打开购票页面。\n")
    exit_code = 0
    try:
        for key in EVENT_ORDER:
            event = EVENTS[key]
            print(f"===== {event['name']} =====")
            result = checker.check(event)
            console_result(event, result, debug=True)
            print()
            log_result(logger, event, result, notified=False)
            if result.status is Status.UNKNOWN:
                exit_code = 2
    finally:
        checker.session.close()
    return exit_code


def run_test_notification() -> int:
    logger = setup_logging(debug=True)
    try:
        mail_config = load_mail_config(required=True)
    except RuntimeError as exc:
        print(f"无法测试通知：{exc}")
        return 2
    event = EVENTS[EVENT_ORDER[0]]
    result = CheckResult(Status.AVAILABLE, "这是手动通知测试，不代表真实库存")
    print("将依次测试 Gmail、Windows 桌面通知、提示音和默认浏览器……")
    outcomes = perform_notifications(event, result, mail_config, logger, test_mode=True)
    for channel, success in outcomes.items():
        print(f"  {channel}: {'成功' if success else '失败（请查看日志）'}")
    return 0 if all(outcomes.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="柏林爱乐 EIF 普通余票监控")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test-notification", action="store_true", help="测试四种通知")
    mode.add_argument("--check-once", action="store_true", help="各检查一次，不正式通知")
    parser.add_argument("--debug", action="store_true", help="显示更详细的安全调试信息")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.test_notification:
        return run_test_notification()
    if args.check_once:
        return run_check_once(args.debug)
    try:
        MonitorApp(debug=args.debug).run()
        return 0
    except RuntimeError as exc:
        print(f"启动失败：{exc}")
        print("请按 README.md 填写项目目录中的 .env 后重试。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
