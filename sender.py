import json
import socket
import ssl
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import websocket

try:
    import pygame
except Exception:  # pragma: no cover - optional runtime dependency
    pygame = None


WS_URL = "wss://localhost:6868"

CLIENT_ID = "Vir3zRiBJOc3zV6bh2Ai6dc1lKvxUzpQL7xpi2yi"
CLIENT_SECRET = "T6JThRloNeXET2TMxeMuXWPXTWHBHBzhWGbKr05f2wU1HZfjr1dT1PziRILeOeUzr9OymxhpFHzRnG4XzpsApYnRLsd7EvzuFrYUDAONck5J5fltiqE4Ed4cW4DHz4gh"

FACIAL_PROFILE_NAME = "facial_blink_clench_demo"
FACIAL_TRAIN_ACTIONS = ["neutral", "clench"]
TRAIN_REPEATS = 1
BLINK_REPEATS = 1
BLINK_GUIDE_DURATION_SEC = 4.0
STATS_WINDOW_SEC = 3.0
STATS_HOP_SEC = 1.0
WINDOW_MIN_COUNT = 13
MEDIA_MAX_WIDTH = 130
MEDIA_MAX_HEIGHT = 126
LOWER_FACE_POWER_THRESHOLD = 0.35
LOWER_FACE_ACTION_MAP = {
    "neutral": "neutral",
    "clench": "clench",
}
DISPLAY_ACTION_LABELS = {
    "neutral": "NEUTRAL",
    "blink": "BLINK",
    "clench": "CLENCH",
}
ACTION_READY_TITLES = {
    "neutral": "就绪：训练静息状态",
    "blink": "就绪：训练递送药物状态",
    "clench": "就绪：训练递送食物状态",
}
ACTION_TRAIN_TITLES = {
    "neutral": "正在训练：静息",
    "blink": "正在训练：递送药物",
    "clench": "正在训练：递送食物",
}
ACTION_TRAIN_LABELS = {
    "neutral": "静息",
    "blink": "递送药物",
    "clench": "递送食物",
}
ACTION_TEST_STATUS = {
    "neutral": "当前：静息状态",
    "blink": "当前：递送药物状态",
    "clench": "当前：递送食物状态",
}
COMMAND_COLORS = {
    "NEUTRAL": (145, 224, 182),
    "BLINK": (87, 196, 255),
    "CLENCH": (255, 212, 84),
    "WAITING": (170, 182, 205),
}
COMMAND_DISPLAY_LABELS = {
    "NEUTRAL": "待机",
    "BLINK": "递送药物",
    "CLENCH": "递送食物",
    "WAITING": "等待",
}
COMMAND_DISPLAY_NOTES = {
    "BLINK": "（眨眼）",
    "CLENCH": "（咬牙）",
}

TARGET_HEADSET_ID = None
RPC_TIMEOUT_SEC = 20
CONNECT_TIMEOUT_SEC = 15
AUTHORIZE_DEBIT = 10

# Update these values directly on the Windows EEG PC before each run.
UDP_COMMAND_ENABLED = True
UDP_COMMAND_HOST = "192.168.117.216"
UDP_COMMAND_PORT = 5005
UDP_COMMAND_SOURCE = "train_new.py"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_websocket_create_connection():
    create_connection = getattr(websocket, "create_connection", None)
    if callable(create_connection):
        return create_connection

    module_path = getattr(websocket, "__file__", "<unknown>")
    raise RuntimeError(
        "The imported 'websocket' module does not provide create_connection(). "
        "This script requires the 'websocket-client' package. "
        f"Currently imported module: {module_path}. "
        "Install it with: pip install websocket-client"
    )


class TrainingVisualizer:
    """Small pygame UI for communication test, facial-expression training, and live output."""

    def __init__(self) -> None:
        self._enabled = pygame is not None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._enter_pressed = threading.Event()
        self._send_test_pressed = threading.Event()
        self._continue_pressed = threading.Event()
        self._assets = self._load_assets()
        self._state = {
            "mode": "idle",
            "title": "Emotiv Blink + Clench",
            "status": "Starting...",
            "detail": "",
            "action": "",
            "power": 0.0,
            "updated_at": "",
            "flash_started_at": time.perf_counter(),
            "stats_decision": "WAITING",
            "stats_neutral_count": 0,
            "stats_blink_count": 0,
            "stats_clench_count": 0,
            "stats_window_start_s": 0.0,
            "stats_window_end_s": 0.0,
            "stats_updated_at": "",
            "comm_target": "",
            "comm_note": "",
        }

    def _load_assets(self) -> dict[str, Any]:
        assets: dict[str, Any] = {
            "neutral_image": None,
            "blink_image": None,
            "clench_image": None,
        }
        if pygame is None:
            return assets

        images_dir = Path(__file__).resolve().parents[1] / "images"
        image_specs = {
            "neutral_image": images_dir / "open.jpg",
            "blink_image": images_dir / "drug.png",
            "clench_image": images_dir / "meal.png",
        }

        for key, path in image_specs.items():
            try:
                if path.exists():
                    image = pygame.image.load(str(path))
                    assets[key] = self._fit_media_surface(image)
            except Exception:
                assets[key] = None

        return assets

    @staticmethod
    def _fit_media_surface(surface: "pygame.Surface") -> "pygame.Surface":
        width, height = surface.get_size()
        scale = min(MEDIA_MAX_WIDTH / float(width), MEDIA_MAX_HEIGHT / float(height))
        target_size = (
            max(1, int(round(width * scale))),
            max(1, int(round(height * scale))),
        )
        return pygame.transform.smoothscale(surface, target_size)

    @staticmethod
    def _pick_font_path(*candidates: str) -> str | None:
        fonts_dir = Path(r"C:\Windows\Fonts")
        for name in candidates:
            path = fonts_dir / name
            if path.exists():
                return str(path)
        return None

    def _build_fonts(self) -> dict[str, "pygame.font.Font"]:
        bold_path = self._pick_font_path(
            "msyhbd.ttc",
            "simhei.ttf",
            "Dengb.ttf",
            "NotoSansSC-VF.ttf",
        )
        regular_path = self._pick_font_path(
            "msyh.ttc",
            "Deng.ttf",
            "simsun.ttc",
            "NotoSansSC-VF.ttf",
        )

        if bold_path and regular_path:
            return {
                "title": pygame.font.Font(bold_path, 36),
                "headline": pygame.font.Font(bold_path, 60),
                "command": pygame.font.Font(bold_path, 34),
                "body": pygame.font.Font(regular_path, 26),
                "note": pygame.font.Font(regular_path, 18),
                "small": pygame.font.Font(regular_path, 20),
            }

        return {
            "title": pygame.font.SysFont("microsoftyaheiui,microsoftyahei,simhei,arial", 36, bold=True),
            "headline": pygame.font.SysFont("microsoftyaheiui,microsoftyahei,simhei,arial", 60, bold=True),
            "command": pygame.font.SysFont("microsoftyaheiui,microsoftyahei,simhei,arial", 34, bold=True),
            "body": pygame.font.SysFont("microsoftyaheiui,microsoftyahei,simhei,arial", 26),
            "note": pygame.font.SysFont("microsoftyaheiui,microsoftyahei,simhei,arial", 18),
            "small": pygame.font.SysFont("microsoftyaheiui,microsoftyahei,simhei,arial", 20),
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="training-visualizer",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def close(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def show_message(self, title: str, status: str, detail: str = "") -> None:
        self._update_state(
            mode="message",
            title=title,
            status=status,
            detail=detail,
        )

    def show_comm_test(self, host: str, port: int, note: str = "") -> None:
        self._send_test_pressed.clear()
        self._continue_pressed.clear()
        self._update_state(
            mode="comm_test",
            # title="UDP Communication Test",
            title="UDP 通信测试",
            # status=f"Target: {host}:{port}",
            status=f"目标地址: {host}:{port}",
            detail=(
                "点击'发送测试信息', 在Linux设备终端中读取到信息后进入测试阶段"
            ),
            comm_target=f"{host}:{port}",
            comm_note=note,
        )

    def wait_for_comm_test_action(self) -> str:
        if not self._enabled:
            choice = input("Type 'test' to send a test message, or 'continue' to start training: ")
            normalized = choice.strip().lower()
            if normalized in {"test", "t"}:
                return "send_test"
            return "continue"

        while not self._stop.is_set():
            if self._send_test_pressed.wait(timeout=0.1):
                self._send_test_pressed.clear()
                return "send_test"
            if self._continue_pressed.is_set():
                self._continue_pressed.clear()
                return "continue"

        raise KeyboardInterrupt("Visualizer window was closed.")

    def show_waiting_for_round(
        self, action: str, round_index: int, repeats: int
    ) -> None:
        self._enter_pressed.clear()
        self._update_state(
            mode="message",
            # title=f"Ready: {DISPLAY_ACTION_LABELS.get(action, action.upper())}",
            # status=f"Round {round_index}/{repeats}",
            # detail="Press Enter in this window to begin this training trial.",
            title=ACTION_READY_TITLES.get(
                action, f"就绪：训练{DISPLAY_ACTION_LABELS.get(action, action.upper())}"
            ),
            status=f"训练轮数 {round_index}/{repeats}",
            detail="按下Enter键开始本轮训练",
            action=action,
        )

    def wait_for_enter(self) -> None:
        if not self._enabled:
            input("Press Enter to begin this training trial...")
            return

        while not self._stop.is_set():
            if self._enter_pressed.wait(timeout=0.1):
                self._enter_pressed.clear()
                return

        raise KeyboardInterrupt("Visualizer window was closed.")


    def start_training(self, action: str, round_index: int, repeats: int) -> None:
        if action == "neutral":
            # prompt = "Relax your face and keep a neutral expression until training completes."
            prompt = "持续放松直至训练结束"
        elif action == "blink":
            # prompt = "Blink when the solid circle flashes."
            prompt = "持续眨眼直至训练结束"
        else:
            # prompt = "Clench your jaw when ready and hold it naturally."
            prompt = "持续咬牙直至训练结束"
        self._update_state(
            mode="train",
            title=ACTION_TRAIN_TITLES.get(
                action, f"正在训练：{DISPLAY_ACTION_LABELS.get(action, action.upper())}"
            ),
            status=f"第 {round_index}/{repeats} 轮训练中...",
            detail=prompt,
            action=action,
            flash_started_at=time.perf_counter(),
        )

    def show_training_result(self, action: str, status: str, detail: str = "") -> None:
        self._update_state(
            mode="message",
            title=f"Training {status}",
            status=DISPLAY_ACTION_LABELS.get(action, action.upper()),
            detail=detail,
            action=action,
        )

    def show_live_output(self, action: str, power: float, raw_value: str) -> None:
        self._update_state(
            mode="live",
            title="测试",
            status=ACTION_TEST_STATUS.get(
                action, f"当前：{DISPLAY_ACTION_LABELS.get(action, action.upper())}"
            ),
            # detail=f"Raw: {raw_value}",
            action=action,
            power=power,
            updated_at=datetime.now().strftime("%H:%M:%S"),
        )

    def show_window_stats(
        self,
        decision: str,
        neutral_count: int,
        blink_count: int,
        clench_count: int,
        *,
        window_start_s: float,
        window_end_s: float,
    ) -> None:
        self._update_state(
            stats_decision=str(decision).upper(),
            stats_neutral_count=int(neutral_count),
            stats_blink_count=int(blink_count),
            stats_clench_count=int(clench_count),
            stats_window_start_s=float(window_start_s),
            stats_window_end_s=float(window_end_s),
            stats_updated_at=datetime.now().strftime("%H:%M:%S"),
        )

    def _update_state(self, **kwargs: Any) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._state.update(kwargs)

    def _snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _run(self) -> None:
        try:
            pygame.init()
            screen = pygame.display.set_mode((960, 540))
            pygame.display.set_caption("Emotiv Facial Command Trainer")
            clock = pygame.time.Clock()
            self._ready.set()
        except Exception as exc:
            self._enabled = False
            self._ready.set()
            print(f"[WARN] visualizer disabled: {exc}")
            return

        fonts = self._build_fonts()

        while not self._stop.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._stop.set()
                elif event.type == pygame.KEYDOWN and event.key in (
                    pygame.K_RETURN,
                    pygame.K_KP_ENTER,
                ):
                    self._enter_pressed.set()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_mouse_click(event.pos)

            state = self._snapshot()
            self._render(screen, fonts, state)
            pygame.display.flip()
            clock.tick(30)

        pygame.quit()

    def _render(
        self,
        screen: "pygame.Surface",
        fonts: dict[str, "pygame.font.Font"],
        state: dict[str, Any],
    ) -> None:
        background = (18, 24, 38)
        panel = (28, 36, 55)
        text = (238, 243, 255)
        accent = (87, 196, 255)
        muted = (170, 182, 205)

        screen.fill(background)
        pygame.draw.rect(screen, panel, pygame.Rect(32, 28, 896, 484), border_radius=20)
        pygame.draw.rect(
            screen,
            (58, 78, 118),
            pygame.Rect(32, 28, 896, 484),
            width=2,
            border_radius=20,
        )

        self._blit_text(screen, fonts["title"], state["title"], (64, 56), text)
        self._blit_text(screen, fonts["body"], state["status"], (64, 108), accent)
        self._draw_wrapped_text(
            screen,
            fonts["small"],
            state.get("detail", ""),
            pygame.Rect(64, 146, 830, 60),
            muted,
        )

        mode = state.get("mode", "idle")
        action = str(state.get("action", "")).lower()

        if mode == "train":
            self._draw_action_symbol(screen, action, center=(480, 310), scale=170)
            self._blit_centered_text(
                screen,
                fonts["headline"],
                ACTION_TRAIN_LABELS.get(
                    action, DISPLAY_ACTION_LABELS.get(action, action.upper())
                ),
                center=(480, 442),
                color=text,
            )
            return

        if mode == "comm_test":
            self._render_comm_test_layout(screen, fonts, state, text, accent, muted)
            return

        if mode == "live":
            self._render_live_layout(screen, fonts, state, action, text, muted)
            return

        self._draw_focus_marker(screen, center=(480, 300))

    def _render_comm_test_layout(
        self,
        screen: "pygame.Surface",
        fonts: dict[str, "pygame.font.Font"],
        state: dict[str, Any],
        text: tuple[int, int, int],
        accent: tuple[int, int, int],
        muted: tuple[int, int, int],
    ) -> None:
        target_rect = pygame.Rect(105, 228, 750, 74)
        send_rect, continue_rect = self._comm_test_button_rects()

        pygame.draw.rect(screen, (36, 46, 70), target_rect, border_radius=18)
        pygame.draw.rect(screen, (74, 102, 150), target_rect, width=2, border_radius=18)
        self._blit_centered_text(
            screen,
            fonts["headline"],
            state.get("comm_target", ""),
            center=target_rect.center,
            color=text,
        )

        note = str(state.get("comm_note", "")).strip()
        if note:
            self._blit_centered_text(
                screen,
                fonts["body"],
                note,
                center=(480, 336),
                color=accent,
            )

        self._draw_button(
            screen,
            fonts["body"],
            send_rect,
            # label="Send Test Message",
            label="发送测试信息",
            fill=(49, 119, 187),
            border=(119, 191, 255),
            text_color=text,
        )
        self._draw_button(
            screen,
            fonts["body"],
            continue_rect,
            # label="Continue to Training",
            label="进入测试阶段",
            fill=(56, 120, 88),
            border=(118, 214, 160),
            text_color=text,
        )
        self._blit_centered_text(
            screen,
            fonts["small"],
            "Linux设备终端会在收到测试信息后记录",
            center=(480, 470),
            color=muted,
        )

    def _render_live_layout(
        self,
        screen: "pygame.Surface",
        fonts: dict[str, "pygame.font.Font"],
        state: dict[str, Any],
        action: str,
        text: tuple[int, int, int],
        muted: tuple[int, int, int],
    ) -> None:
        left_rect = pygame.Rect(64, 182, 236, 284)
        right_rect = pygame.Rect(324, 182, 540, 284)
        media_rect = pygame.Rect(694, 238, 130, 126)
        decision_rect = pygame.Rect(348, 236, 250, 100)
        note_rect = pygame.Rect(348, 330, 250, 26)
        stats_left_x = 376
        stats_right_x = 642
        stats_y0 = 366
        stats_gap = 28

        pygame.draw.rect(screen, (36, 46, 70), left_rect, border_radius=18)
        pygame.draw.rect(screen, (36, 46, 70), right_rect, border_radius=18)
        pygame.draw.rect(screen, (74, 102, 150), left_rect, width=2, border_radius=18)
        pygame.draw.rect(screen, (74, 102, 150), right_rect, width=2, border_radius=18)

        self._blit_text(screen, fonts["body"], "实时结果", (88, 202), text)
        self._draw_action_symbol(screen, action, center=(left_rect.centerx, 315), scale=96)
        power = float(state.get("power", 0.0))
        self._blit_centered_text(
            screen,
            fonts["headline"],
            f"{power:.2f}",
            center=(left_rect.centerx, 388),
            color=text,
        )
        updated_at = state.get("updated_at", "")
        if updated_at:
            self._blit_centered_text(
                screen,
                fonts["small"],
                f"状态更新于 {updated_at}",
                center=(left_rect.centerx, 438),
                color=muted,
            )

        self._blit_text(screen, fonts["body"], "指令", (348, 202), text)
        decision = str(state.get("stats_decision", "WAITING")).upper()
        decision_color = COMMAND_COLORS.get(decision, muted)
        decision_label = COMMAND_DISPLAY_LABELS.get(decision, decision)
        decision_note = COMMAND_DISPLAY_NOTES.get(decision, "")
        self._blit_centered_text(
            screen,
            fonts["command"],
            decision_label,
            center=decision_rect.center,
            color=decision_color,
        )
        if decision_note:
            self._blit_centered_text(
                screen,
                fonts["note"],
                decision_note,
                center=note_rect.center,
                color=muted,
            )
        self._draw_stats_media(screen, fonts, decision, media_rect)

        neutral_count = int(state.get("stats_neutral_count", 0))
        blink_count = int(state.get("stats_blink_count", 0))
        clench_count = int(state.get("stats_clench_count", 0))
        window_start_s = float(state.get("stats_window_start_s", 0.0))
        window_end_s = float(state.get("stats_window_end_s", 0.0))
        self._blit_text(screen, fonts["small"], f"blink: {blink_count}", (stats_left_x, stats_y0), text)
        self._blit_text(
            screen,
            fonts["small"],
            f"neutral: {neutral_count}",
            (stats_left_x, stats_y0 + stats_gap),
            text,
        )
        self._blit_text(
            screen,
            fonts["small"],
            f"clench: {clench_count}",
            (stats_left_x, stats_y0 + stats_gap * 2),
            text,
        )
        self._blit_text(
            screen,
            fonts["small"],
            f"窗口: {window_start_s:.2f}-{window_end_s:.2f}s",
            (stats_right_x, stats_y0 + stats_gap),
            muted,
        )

    def _draw_action_symbol(
        self,
        screen: "pygame.Surface",
        action: str,
        center: tuple[int, int],
        scale: int,
    ) -> None:
        if action == "left":
            self._draw_arrow(screen, center, scale, direction="left", color=(77, 181, 255))
        elif action == "right":
            self._draw_arrow(screen, center, scale, direction="right", color=(255, 176, 76))
        elif action == "neutral":
            pygame.draw.circle(screen, (145, 224, 182), center, scale // 2, width=14)
        elif action == "blink":
            pygame.draw.circle(screen, (87, 196, 255), center, scale // 2, width=14)
            pygame.draw.circle(screen, (255, 255, 255), center, scale // 6)
        elif action == "clench":
            pygame.draw.circle(screen, (255, 212, 84), center, scale // 2)
            pygame.draw.circle(screen, (255, 255, 255), center, scale // 2, width=6)
        else:
            self._draw_focus_marker(screen, center)

    def _draw_stats_media(
        self,
        screen: "pygame.Surface",
        fonts: dict[str, "pygame.font.Font"],
        decision: str,
        rect: "pygame.Rect",
    ) -> None:
        if decision == "NEUTRAL":
            image = self._assets.get("neutral_image")
        elif decision == "BLINK":
            image = self._assets.get("blink_image")
        elif decision == "CLENCH":
            image = self._assets.get("clench_image")
        else:
            image = None

        if image is not None:
            try:
                image_rect = image.get_rect(center=rect.center)
                screen.blit(image, image_rect.topleft)
                return
            except Exception:
                pass

        self._blit_centered_text(screen, fonts["body"], "Waiting", rect.center, (170, 182, 205))

    @staticmethod
    def _comm_test_button_rects() -> tuple["pygame.Rect", "pygame.Rect"]:
        send_rect = pygame.Rect(220, 382, 220, 64)
        continue_rect = pygame.Rect(520, 382, 220, 64)
        return send_rect, continue_rect

    def _handle_mouse_click(self, pos: tuple[int, int]) -> None:
        state = self._snapshot()
        if state.get("mode") != "comm_test":
            return

        send_rect, continue_rect = self._comm_test_button_rects()
        if send_rect.collidepoint(pos):
            self._send_test_pressed.set()
        elif continue_rect.collidepoint(pos):
            self._continue_pressed.set()

    def _draw_focus_marker(
        self, screen: "pygame.Surface", center: tuple[int, int]
    ) -> None:
        pygame.draw.circle(screen, (95, 118, 160), center, 72, width=8)
        pygame.draw.circle(screen, (95, 118, 160), center, 12)

    def _draw_button(
        self,
        screen: "pygame.Surface",
        font: "pygame.font.Font",
        rect: "pygame.Rect",
        *,
        label: str,
        fill: tuple[int, int, int],
        border: tuple[int, int, int],
        text_color: tuple[int, int, int],
    ) -> None:
        pygame.draw.rect(screen, fill, rect, border_radius=18)
        pygame.draw.rect(screen, border, rect, width=2, border_radius=18)
        self._blit_centered_text(screen, font, label, rect.center, text_color)

    def _draw_arrow(
        self,
        screen: "pygame.Surface",
        center: tuple[int, int],
        scale: int,
        *,
        direction: str,
        color: tuple[int, int, int],
    ) -> None:
        cx, cy = center
        tail = int(scale * 0.85)
        shaft = int(scale * 0.28)
        tip = int(scale * 0.62)
        wing = int(scale * 0.48)

        if direction == "left":
            points = [
                (cx + tail, cy - shaft),
                (cx, cy - shaft),
                (cx, cy - wing),
                (cx - tip, cy),
                (cx, cy + wing),
                (cx, cy + shaft),
                (cx + tail, cy + shaft),
            ]
        else:
            points = [
                (cx - tail, cy - shaft),
                (cx, cy - shaft),
                (cx, cy - wing),
                (cx + tip, cy),
                (cx, cy + wing),
                (cx, cy + shaft),
                (cx - tail, cy + shaft),
            ]

        pygame.draw.polygon(screen, color, points)
        pygame.draw.polygon(screen, (255, 255, 255), points, width=5)

    def _blit_text(
        self,
        screen: "pygame.Surface",
        font: "pygame.font.Font",
        value: str,
        pos: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        surface = font.render(value, True, color)
        screen.blit(surface, pos)

    def _blit_centered_text(
        self,
        screen: "pygame.Surface",
        font: "pygame.font.Font",
        value: str,
        center: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        surface = font.render(value, True, color)
        rect = surface.get_rect(center=center)
        screen.blit(surface, rect.topleft)

    def _draw_wrapped_text(
        self,
        screen: "pygame.Surface",
        font: "pygame.font.Font",
        value: str,
        rect: "pygame.Rect",
        color: tuple[int, int, int],
    ) -> None:
        words = value.split()
        if not words:
            return

        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if font.size(candidate)[0] <= rect.width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

        y = rect.y
        for line in lines[:3]:
            self._blit_text(screen, font, line, (rect.x, y), color)
            y += font.get_linesize() + 4


class CortexClient:
    def __init__(self, ws_url: str):
        create_connection = _resolve_websocket_create_connection()
        self.ws = create_connection(
            ws_url,
            sslopt={"cert_reqs": ssl.CERT_NONE},
            timeout=RPC_TIMEOUT_SEC,
        )
        self.rpc_id = 1

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def _next_id(self) -> int:
        rid = self.rpc_id
        self.rpc_id += 1
        return rid

    def call(self, method: str, params: dict | None = None) -> Any:
        req_id = self._next_id()
        payload = {
            "id": req_id,
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self.ws.send(json.dumps(payload))

        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)

            if msg.get("id") != req_id:
                continue

            if "error" in msg:
                raise RuntimeError(
                    f"{method} failed: {json.dumps(msg['error'], ensure_ascii=False)}"
                )

            return msg.get("result", {})

    def recv_stream(self) -> dict:
        raw = self.ws.recv()
        return json.loads(raw)


class UdpCommandSender:
    def __init__(
        self,
        *,
        enabled: bool,
        host: str,
        port: int,
        source: str,
    ) -> None:
        self.enabled = enabled
        self.host = host
        self.port = port
        self.source = source
        self._sock: socket.socket | None = None
        self._seq = 0

        if not enabled:
            return

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None

    def send_command(
        self,
        command: str,
        *,
        neutral_count: int,
        blink_count: int,
        clench_count: int,
        window_start_s: float,
        window_end_s: float,
    ) -> None:
        if not self.enabled or self._sock is None:
            return

        self._seq += 1
        payload = {
            "seq": self._seq,
            "cmd": str(command).upper(),
            "neutral_count": int(neutral_count),
            "blink_count": int(blink_count),
            "clench_count": int(clench_count),
            "window_start_s": round(float(window_start_s), 3),
            "window_end_s": round(float(window_end_s), 3),
            "sent_at": datetime.now().isoformat(timespec="milliseconds"),
            "source": self.source,
        }

        try:
            self._sock.sendto(
                json.dumps(payload, ensure_ascii=True).encode("utf-8"),
                (self.host, self.port),
            )
            print(
                f"[UDP] sent {payload['cmd']} seq={payload['seq']} "
                f"to {self.host}:{self.port}"
            )
        except OSError as exc:
            print(f"[WARN] UDP send failed: {exc}")

    def send_test_message(self) -> None:
        if not self.enabled or self._sock is None:
            return

        self._seq += 1
        payload = {
            "seq": self._seq,
            "cmd": "TEST",
            "sent_at": datetime.now().isoformat(timespec="milliseconds"),
            "source": self.source,
            "message": "UDP link check from Windows train_new.py",
        }

        try:
            self._sock.sendto(
                json.dumps(payload, ensure_ascii=True).encode("utf-8"),
                (self.host, self.port),
            )
            print(
                f"[UDP] sent TEST seq={payload['seq']} "
                f"to {self.host}:{self.port}"
            )
        except OSError as exc:
            print(f"[WARN] UDP test send failed: {exc}")


def run_comm_test_phase(
    visualizer: TrainingVisualizer, command_sender: UdpCommandSender
) -> None:
    if not command_sender.enabled:
        print("[INFO] UDP command output disabled; skipping communication test.")
        return

    # note = "Click Send Test Message before starting training."
    note = "请在训练阶段前点击'发送测试信息'"
    while True:
        visualizer.show_comm_test(command_sender.host, command_sender.port, note=note)
        action = visualizer.wait_for_comm_test_action()

        if action == "send_test":
            command_sender.send_test_message()
            # note = "Test message sent. Check the Linux terminal, then click Continue."
            note = "测试信息已发送, 请检查Linux设备终端"
            continue

        if action == "continue":
            return


def request_access(client: CortexClient) -> bool:
    result = client.call(
        "requestAccess",
        {
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
        },
    )
    return bool(result.get("accessGranted", False))


def authorize(client: CortexClient) -> str:
    result = client.call(
        "authorize",
        {
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
            "debit": AUTHORIZE_DEBIT,
        },
    )
    token = result.get("cortexToken")
    if not token:
        raise RuntimeError(f"authorize succeeded but no cortexToken found: {result}")
    return token


def query_headsets(client: CortexClient) -> list[dict]:
    result = client.call("queryHeadsets", {})
    if not isinstance(result, list):
        raise RuntimeError(f"queryHeadsets unexpected result: {result}")
    return result


def wait_until_connected(
    client: CortexClient,
    headset_id: str,
    timeout_sec: int = CONNECT_TIMEOUT_SEC,
) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for hs in query_headsets(client):
            if hs.get("id") == headset_id and hs.get("status") == "connected":
                return hs
        time.sleep(1.0)
    raise TimeoutError(f"Headset {headset_id} did not become connected in time.")


def choose_headset(headsets: list[dict]) -> dict:
    if not headsets:
        raise RuntimeError("No headset found.")

    if TARGET_HEADSET_ID:
        for hs in headsets:
            if hs.get("id") == TARGET_HEADSET_ID:
                return hs
        raise RuntimeError(f"TARGET_HEADSET_ID not found: {TARGET_HEADSET_ID}")

    connected = [h for h in headsets if h.get("status") == "connected"]
    if connected:
        return connected[0]

    discovered = [h for h in headsets if h.get("status") == "discovered"]
    if discovered:
        return discovered[0]

    return headsets[0]


def connect_headset_if_needed(client: CortexClient, headset: dict) -> dict:
    headset_id = headset["id"]
    status = headset.get("status")
    connected_by = headset.get("connectedBy")

    if connected_by == "usb":
        raise RuntimeError("USB-connected headset cannot open session.")

    if status == "connected":
        return headset

    if status == "discovered":
        client.call(
            "controlDevice",
            {
                "command": "connect",
                "headset": headset_id,
            },
        )
        return wait_until_connected(client, headset_id)

    raise RuntimeError(f"Unsupported headset status: {status}")


def create_active_session(client: CortexClient, token: str, headset_id: str) -> dict:
    return client.call(
        "createSession",
        {
            "cortexToken": token,
            "headset": headset_id,
            "status": "active",
        },
    )


def subscribe_streams(
    client: CortexClient,
    token: str,
    session_id: str,
    streams: list[str],
) -> dict:
    result = client.call(
        "subscribe",
        {
            "cortexToken": token,
            "session": session_id,
            "streams": streams,
        },
    )
    failures = result.get("failure", [])
    if failures:
        raise RuntimeError(
            f"subscribe failed: {json.dumps(failures, ensure_ascii=False)}"
        )
    return result


def get_detection_info(client: CortexClient, detection: str = "facialExpression") -> dict:
    return client.call("getDetectionInfo", {"detection": detection})


def query_profile(client: CortexClient, token: str) -> list[dict]:
    result = client.call("queryProfile", {"cortexToken": token})
    if not isinstance(result, list):
        raise RuntimeError(f"queryProfile unexpected result: {result}")
    return result


def get_current_profile(client: CortexClient, token: str, headset_id: str) -> dict:
    return client.call(
        "getCurrentProfile",
        {
            "cortexToken": token,
            "headset": headset_id,
        },
    )


def setup_profile(
    client: CortexClient,
    token: str,
    status: str,
    profile: str = "",
    headset_id: str | None = None,
) -> dict:
    params = {
        "cortexToken": token,
        "status": status,
        "profile": profile,
    }

    if status in {"load", "save"}:
        if not headset_id:
            raise ValueError(f"headset_id is required for status={status}")
        params["headset"] = headset_id
    elif status == "unload":
        if not headset_id:
            raise ValueError("headset_id is required for unload")
        params["headset"] = headset_id
        params["profile"] = ""
    elif status not in {"create", "delete"}:
        raise ValueError(f"Unsupported setupProfile status: {status}")

    return client.call("setupProfile", params)


def load_guest_profile(client: CortexClient, token: str, headset_id: str) -> dict:
    return client.call(
        "loadGuestProfile",
        {
            "cortexToken": token,
            "headset": headset_id,
        },
    )


def ensure_profile_loaded(
    client: CortexClient,
    token: str,
    headset_id: str,
    profile_name: str,
) -> None:
    profiles = query_profile(client, token)
    profile_names = [p.get("name") for p in profiles]

    current = get_current_profile(client, token, headset_id)
    current_name = current.get("name")
    loaded_by_this_app = current.get("loadedByThisApp", False)

    if profile_name in profile_names:
        if current_name == profile_name and loaded_by_this_app:
            return

        if current_name and loaded_by_this_app and current_name != profile_name:
            setup_profile(client, token, "unload", "", headset_id)

        current = get_current_profile(client, token, headset_id)
        current_name = current.get("name")
        loaded_by_this_app = current.get("loadedByThisApp", False)

        if current_name and not loaded_by_this_app:
            raise RuntimeError(
                f"A profile is already loaded by another app: {current_name}."
            )

        setup_profile(client, token, "load", profile_name, headset_id)
        return

    if current_name and loaded_by_this_app:
        setup_profile(client, token, "unload", "", headset_id)

    current = get_current_profile(client, token, headset_id)
    current_name = current.get("name")
    loaded_by_this_app = current.get("loadedByThisApp", False)

    if current_name and not loaded_by_this_app:
        raise RuntimeError(
            f"A profile is already loaded by another app: {current_name}."
        )

    load_guest_profile(client, token, headset_id)


def training_control(
    client: CortexClient,
    token: str,
    session_id: str,
    action: str,
    status: str,
    detection: str = "facialExpression",
) -> dict:
    return client.call(
        "training",
        {
            "cortexToken": token,
            "session": session_id,
            "detection": detection,
            "action": action,
            "status": status,
        },
    )


def wait_training_result(client: CortexClient, timeout_sec: int = 20) -> str:
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        msg = client.recv_stream()

        if "sys" in msg:
            sys_data = msg["sys"]
            print("[SYS]", sys_data)

            text = " ".join(map(str, sys_data)).lower()

            if "started" in text:
                continue
            if "succeeded" in text:
                return "succeeded"
            if "failed" in text:
                return "failed"
            if "completed" in text:
                return "completed"

    raise TimeoutError("Timed out waiting for training sys event.")


def facial_expression_signature_type(
    client: CortexClient,
    token: str,
    session_id: str,
    status: str,
    signature: str | None = None,
) -> dict:
    params = {
        "cortexToken": token,
        "session": session_id,
        "status": status,
    }
    if signature is not None:
        params["signature"] = signature
    return client.call(
        "facialExpressionSignatureType",
        params,
    )


def run_blink_guided_phase(
    visualizer: TrainingVisualizer,
    repeats: int = BLINK_REPEATS,
    duration_sec: float = BLINK_GUIDE_DURATION_SEC,
) -> None:
    print("\n" + "=" * 60)
    print(f"[BLINK] guided blink phase, repeats={repeats}")

    for i in range(repeats):
        round_index = i + 1
        print(f"\n[BLINK] Round {round_index}/{repeats}")
        visualizer.show_waiting_for_round("blink", round_index, repeats)
        print(
            "[BLINK] Press Enter in the visualizer window to start the blink round."
        )
        visualizer.wait_for_enter()

        visualizer.start_training("blink", round_index, repeats)
        print(
            f"[BLINK] flashing solid circle for {duration_sec:.1f}s. "
            "Blink in sync with the visual cue."
        )
        time.sleep(duration_sec)
        visualizer.show_training_result(
            "blink",
            "DONE",
            f"Round {round_index}/{repeats} complete. Proceed when ready.",
        )

    print("[BLINK] guided blink phase complete.")


def train_one_action(
    client: CortexClient,
    token: str,
    session_id: str,
    action: str,
    visualizer: TrainingVisualizer,
    repeats: int = TRAIN_REPEATS,
    detection: str = "facialExpression",
    display_action: str | None = None,
) -> int:
    accepted = 0
    ui_action = display_action or action

    print("\n" + "=" * 60)
    print(f"[TRAIN] action={action}, ui_action={ui_action}, repeats={repeats}")

    for i in range(repeats):
        round_index = i + 1
        print(f"\n[TRAIN] Round {round_index}/{repeats} for action={action}")
        visualizer.show_waiting_for_round(ui_action, round_index, repeats)
        print(
            f"[TRAIN] Press Enter in the visualizer window to start "
            f"{ui_action} (round {round_index}/{repeats})."
        )
        visualizer.wait_for_enter()

        visualizer.start_training(ui_action, round_index, repeats)
        training_control(client, token, session_id, action, "start", detection=detection)
        result = wait_training_result(client, timeout_sec=25)

        if result != "succeeded":
            detail = f"Round {round_index}/{repeats} ended with result={result}."
            print(f"[TRAIN] round failed: {result}")
            visualizer.show_training_result(ui_action, "FAILED", detail)
            continue

        print("[TRAIN] accept")
        training_control(client, token, session_id, action, "accept", detection=detection)
        result2 = wait_training_result(client, timeout_sec=15)
        if result2 == "completed":
            accepted += 1
            detail = (
                f"Round {round_index}/{repeats} accepted | "
                f"accepted={accepted}/{repeats}"
            )
            print(f"[TRAIN] accepted rounds = {accepted}")
            visualizer.show_training_result(ui_action, "ACCEPTED", detail)
        else:
            detail = (
                f"Accept sent but completion was not confirmed: {result2} | "
                f"accepted={accepted}/{repeats}"
            )
            print(f"[TRAIN] accept sent but no completed: {result2}")
            visualizer.show_training_result(ui_action, "INCOMPLETE", detail)

    print(f"[TRAIN] action={action} done. accepted={accepted}/{repeats}")
    visualizer.show_training_result(
        ui_action,
        "DONE",
        f"Training finished with accepted={accepted}/{repeats}.",
    )
    return accepted


def parse_com_output(stream_data: Any) -> tuple[str, float]:
    if isinstance(stream_data, list):
        action = str(stream_data[0]) if stream_data else "unknown"
        power = _safe_float(stream_data[1] if len(stream_data) > 1 else 0.0)
        return action, power

    if isinstance(stream_data, dict):
        action = str(
            stream_data.get("action", stream_data.get("command", stream_data))
        )
        power = _safe_float(
            stream_data.get("power", stream_data.get("score", 0.0))
        )
        return action, power

    return str(stream_data), 0.0


def parse_fac_output(stream_data: Any) -> tuple[str, float] | None:
    if isinstance(stream_data, list):
        if not stream_data:
            return None
        eye_action = str(stream_data[0] if len(stream_data) > 0 else "").strip().lower()
        eye_power = _safe_float(stream_data[1] if len(stream_data) > 1 else 1.0)
        if eye_action == "blink":
            return "blink", eye_power
        lower_action = str(stream_data[3] if len(stream_data) > 3 else "").strip().lower()
        lower_power = _safe_float(stream_data[4] if len(stream_data) > 4 else 0.0)
        if lower_action in LOWER_FACE_ACTION_MAP and lower_power >= LOWER_FACE_POWER_THRESHOLD:
            return LOWER_FACE_ACTION_MAP[lower_action], lower_power
        return "neutral", lower_power

    if isinstance(stream_data, dict):
        eye_action = str(
            stream_data.get("eyeAct", "")
        ).strip().lower()
        eye_power = _safe_float(
            stream_data.get("eyePow", stream_data.get("upperFacePower", 1.0)),
            default=1.0,
        )
        if eye_action == "blink":
            return "blink", eye_power
        lower_action = str(
            stream_data.get("lAct", stream_data.get("action", ""))
        ).strip().lower()
        lower_power = _safe_float(
            stream_data.get("lPow", stream_data.get("power", stream_data.get("score", 0.0))),
            default=0.0,
        )
        if lower_action in LOWER_FACE_ACTION_MAP and lower_power >= LOWER_FACE_POWER_THRESHOLD:
            return LOWER_FACE_ACTION_MAP[lower_action], lower_power
        return "neutral", lower_power

    return None


def live_classify_loop(
    client: CortexClient,
    visualizer: TrainingVisualizer,
    command_sender: UdpCommandSender,
) -> None:
    print("\n[TEST] neutral/blink/clench live test started. Ctrl+C to stop.\n")
    result_history: deque[tuple[float, str]] = deque()
    loop_start_mono = time.perf_counter()
    next_window_close = loop_start_mono + STATS_WINDOW_SEC
    visualizer.show_message(
        "Live Output",
        "Listening to facial-expression stream...",
    )
    visualizer.show_window_stats(
        "WAITING",
        0,
        0,
        0,
        window_start_s=0.0,
        window_end_s=0.0,
    )

    def _finalize_due_windows(now_mono: float) -> None:
        nonlocal next_window_close

        while now_mono >= next_window_close:
            window_end = next_window_close
            window_start = window_end - STATS_WINDOW_SEC

            neutral_count = 0
            blink_count = 0
            clench_count = 0
            for ts_mono, label in result_history:
                if window_start <= ts_mono <= window_end:
                    if label == "neutral":
                        neutral_count += 1
                    elif label == "blink":
                        blink_count += 1
                    elif label == "clench":
                        clench_count += 1

            if blink_count >= WINDOW_MIN_COUNT:
                decision = "BLINK"
            elif clench_count >= WINDOW_MIN_COUNT:
                decision = "CLENCH"
            else:
                decision = "NEUTRAL"
            visualizer.show_window_stats(
                decision,
                neutral_count,
                blink_count,
                clench_count,
                window_start_s=window_start - loop_start_mono,
                window_end_s=window_end - loop_start_mono,
            )
            command_sender.send_command(
                decision,
                neutral_count=neutral_count,
                blink_count=blink_count,
                clench_count=clench_count,
                window_start_s=window_start - loop_start_mono,
                window_end_s=window_end - loop_start_mono,
            )

            prune_before = window_end - STATS_WINDOW_SEC
            while result_history and result_history[0][0] < prune_before:
                result_history.popleft()

            next_window_close += STATS_HOP_SEC

    def _register_result(action: str, sample_time_mono: float) -> None:
        normalized = action.strip().lower()
        if normalized not in {"neutral", "blink", "clench"}:
            return

        result_history.append((sample_time_mono, normalized))
        _finalize_due_windows(sample_time_mono)

    while True:
        msg = client.recv_stream()
        ts = datetime.now().isoformat(timespec="milliseconds")
        sample_time_mono = time.perf_counter()

        if "fac" in msg:
            stream_data = msg["fac"]
            fac_result = parse_fac_output(stream_data)
            if fac_result is not None:
                action, power = fac_result
                print(f"{ts} | FAC | {stream_data}")
                visualizer.show_live_output(action, power, str(stream_data))
                _register_result(action, sample_time_mono)


def main() -> None:
    if CLIENT_ID == "YOUR_CLIENT_ID" or not CLIENT_ID.strip():
        print("Please set CLIENT_ID before running this script.", file=sys.stderr)
        sys.exit(1)

    if CLIENT_SECRET == "YOUR_CLIENT_SECRET" or not CLIENT_SECRET.strip():
        print("Please set CLIENT_SECRET before running this script.", file=sys.stderr)
        sys.exit(1)

    visualizer = TrainingVisualizer()
    visualizer.start()
    command_sender = UdpCommandSender(
        enabled=UDP_COMMAND_ENABLED,
        host=UDP_COMMAND_HOST,
        port=UDP_COMMAND_PORT,
        source=UDP_COMMAND_SOURCE,
    )
    run_comm_test_phase(visualizer, command_sender)
    visualizer.show_message(
        "Emotiv Clench + Blink",
        "连接到Cortex...",
    )

    client = CortexClient(WS_URL)

    try:
        print("[1/9] requestAccess ...")
        granted = request_access(client)
        print("    accessGranted =", granted)
        if not granted:
            raise RuntimeError("requestAccess not granted.")

        print("[2/9] authorize ...")
        token = authorize(client)
        print("    cortexToken acquired")

        print("[3/9] queryHeadsets ...")
        headsets = query_headsets(client)
        for hs in headsets:
            print(
                "   -",
                hs.get("id"),
                "| status =",
                hs.get("status"),
                "| connectedBy =",
                hs.get("connectedBy"),
            )

        headset = choose_headset(headsets)
        headset = connect_headset_if_needed(client, headset)
        print("[4/9] headset ready:", headset["id"])

        print("[5/9] createSession(active) ...")
        session = create_active_session(client, token, headset["id"])
        session_id = session.get("id")
        if not session_id:
            raise RuntimeError(f"createSession did not return session id: {session}")
        print("    session_id =", session_id)

        print("[6/9] load profile ...")
        ensure_profile_loaded(client, token, headset["id"], FACIAL_PROFILE_NAME)

        print("[7/9] subscribe sys + fac ...")
        subscribe_streams(client, token, session_id, ["sys", "fac"])

        print("[8/9] getDetectionInfo ...")
        info = get_detection_info(client, "facialExpression")
        actions = info.get("actions", [])
        print("    available actions:", actions)

        train_actions = [a for a in FACIAL_TRAIN_ACTIONS if a in actions]
        print("    will train facialExpression:", train_actions)

        for action in train_actions:
            train_one_action(
                client,
                token,
                session_id,
                action,
                visualizer=visualizer,
                repeats=TRAIN_REPEATS,
                detection="facialExpression",
                display_action=action,
            )

        print("[8.5/9] set facial expression signature to trained ...")
        signature_result = facial_expression_signature_type(
            client,
            token,
            session_id,
            status="set",
            signature="trained",
        )
        print("    signature result:", signature_result)

        print("\n[INFO] Starting guided blink phase.")
        run_blink_guided_phase(visualizer, repeats=BLINK_REPEATS)

        print("[9/9] save profile ...")
        result = setup_profile(client, token, "save", FACIAL_PROFILE_NAME, headset["id"])
        print("    save result:", result)

        print("\n[INFO] Facial-expression preparation finished. Switching to live test view.")
        if command_sender.enabled:
            print(
                f"[INFO] UDP command output enabled -> "
                f"{command_sender.host}:{command_sender.port}"
            )
        else:
            print("[INFO] UDP command output disabled.")
        live_classify_loop(client, visualizer, command_sender)

    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        client.close()
        command_sender.close()
        visualizer.close()


if __name__ == "__main__":
    main()
