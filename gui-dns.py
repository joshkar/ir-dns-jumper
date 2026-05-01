import asyncio
import subprocess
import sys
import time
import json
import os
import re
import platform
import flet as ft
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ping3 import ping
import dns.resolver

SYSTEM = platform.system().lower()   # "darwin" | "windows" | "linux"
CUSTOM_DNS_FILE = os.path.expanduser("~/.dns_manager_custom.json")

# ================= PRIVILEGE ELEVATION =================

def ensure_privileges():
    """
    Make sure the app runs with admin/root privileges before anything starts.

    Windows → checks IsUserAnAdmin(); if not admin, triggers UAC and exits.
    macOS   → checks euid==0; if not root, replaces process with `sudo python …`.
    Linux   → same as macOS (uses pkexec as fallback for GUI environments).
    """
    if SYSTEM == "windows":
        try:
            import ctypes
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False

        if not is_admin:
            import ctypes
            # ShellExecuteW with "runas" triggers the UAC dialog
            params = " ".join(f'"{a}"' for a in sys.argv)
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            # ret > 32 means success; the elevated copy is now starting
            sys.exit(0 if ret > 32 else 1)

    else:
        # macOS / Linux — 0 means root
        if os.geteuid() != 0:
            if SYSTEM == "linux":
                # Prefer pkexec in graphical sessions (shows a polkit dialog)
                pkexec = subprocess.run(
                    ["which", "pkexec"], capture_output=True, text=True
                ).stdout.strip()
                if pkexec:
                    os.execvp("pkexec", ["pkexec", sys.executable] + sys.argv)

            # macOS & Linux fallback: replace this process with sudo
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)

def load_custom_dns() -> dict:
    try:
        with open(CUSTOM_DNS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_custom_dns(entries: dict):
    try:
        with open(CUSTOM_DNS_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception:
        pass

def is_valid_ip(ip: str) -> bool:
    pattern = r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$"
    m = re.match(pattern, ip.strip())
    if not m:
        return False
    return all(0 <= int(g) <= 255 for g in m.groups())

def get_active_interfaces() -> list[str]:
    try:
        if SYSTEM == "darwin":
            return _interfaces_macos()
        elif SYSTEM == "windows":
            return _interfaces_windows()
        else:
            return _interfaces_linux()
    except Exception:
        return []

def _interfaces_macos() -> list[str]:
    lookup = subprocess.run(
        ["networksetup", "-listallhardwareports"],
        capture_output=True, text=True
    )
    blocks = lookup.stdout.strip().split("\n\n")
    port_to_device = {}
    for block in blocks:
        port = device = None
        for line in block.splitlines():
            if line.startswith("Hardware Port:"):
                port = line.split(":", 1)[-1].strip()
            elif line.startswith("Device:"):
                device = line.split(":", 1)[-1].strip()
        if port and device:
            port_to_device[device] = port

    active = []
    for device, port in port_to_device.items():
        out = subprocess.run(["ifconfig", device], capture_output=True, text=True).stdout
        if "status: active" in out or ("inet " in out and "flags=" in out and "UP" in out):
            active.append(port)
    return active

def _interfaces_windows() -> list[str]:
    result = subprocess.run(
        ["netsh", "interface", "show", "interface"],
        capture_output=True, text=True
    )
    interfaces = []
    for line in result.stdout.splitlines():
        parts = line.split()
        # Columns: Admin State | State | Type | Interface Name
        if len(parts) >= 4 and parts[1].lower() == "connected":
            interfaces.append(" ".join(parts[3:]))
    return interfaces

def _interfaces_linux() -> list[str]:
    # Try nmcli first (NetworkManager)
    r = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,STATE", "con", "show", "--active"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        ifaces = []
        for line in r.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "activated":
                ifaces.append(parts[0])
        if ifaces:
            return ifaces
    # Fallback: ip link
    r = subprocess.run(["ip", "-o", "link", "show", "up"], capture_output=True, text=True)
    ifaces = []
    for line in r.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 2:
            name = parts[1].strip().split("@")[0]
            if name != "lo":
                ifaces.append(name)
    return ifaces

DNS_SERVERS = {
    "Shekan":   ["178.22.122.101", "185.51.200.1"],
    "Google":   ["8.8.8.8", "8.8.4.4"],
    "Electro":  ["78.157.42.101", "78.157.42.100"],
    "Begzar":   ["185.55.226.26", "185.55.225.25"],
    "Radar":    ["10.202.10.10", "10.202.10.11"],
    "Shellter": ["94.103.125.157", "94.103.125.158"],
    "Beshkan":  ["181.41.194.177", "181.41.194.186"],
    "Shatell":  ["85.15.1.14", "85.15.1.15"],
}

# ================= METRICS =================

def measure_latency(server: str) -> float | None:
    try:
        times = []
        for _ in range(3):
            rtt = ping(server, timeout=2.5, unit='ms')
            if rtt is not None and isinstance(rtt, (int, float)):
                times.append(rtt)
            time.sleep(0.3)
        return sum(times) / len(times) if times else None
    except Exception:
        return None

def measure_response_time(server: str) -> float | None:
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [server]
        resolver.timeout = 4.0
        resolver.lifetime = 5.5
        start = time.perf_counter()
        resolver.resolve("example.com", "A")
        elapsed = (time.perf_counter() - start) * 1000
        return elapsed if elapsed >= 1 else None
    except Exception:
        return None

def fetch_metrics(name: str, servers: list[str]) -> dict:
    ip = servers[0]
    lat = measure_latency(ip)
    resp = measure_response_time(ip)
    return {"name": name, "servers": servers, "lat": lat, "resp": resp}

# ================= CROSS-PLATFORM COMMANDS =================

def run_elevated(cmd: list[str]) -> tuple[bool, str]:
    """Run a command with elevated privileges (sudo on Unix, direct on Windows)."""
    try:
        full_cmd = cmd if SYSTEM == "windows" else ["sudo"] + cmd
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        return result.returncode == 0, result.stderr.strip()
    except Exception as e:
        return False, str(e)

def cmd_set_dns(interface: str, servers: list[str]) -> tuple[bool, str]:
    if SYSTEM == "darwin":
        ok, err = run_elevated(["networksetup", "-setdnsservers", interface, "empty"])
        if not ok:
            return False, err
        return run_elevated(["networksetup", "-setdnsservers", interface] + servers)

    elif SYSTEM == "windows":
        ok, err = run_elevated([
            "netsh", "interface", "ip", "set", "dns",
            interface, "static", servers[0]
        ])
        if not ok:
            return False, err
        for i, ip in enumerate(servers[1:], start=2):
            run_elevated([
                "netsh", "interface", "ip", "add", "dns",
                interface, ip, f"index={i}"
            ])
        return True, ""

    else:  # Linux
        dns_str = " ".join(servers)
        ok, err = run_elevated(["nmcli", "con", "mod", interface, "ipv4.dns", dns_str])
        if not ok:
            return False, err
        run_elevated(["nmcli", "con", "mod", interface, "ipv4.ignore-auto-dns", "yes"])
        return run_elevated(["nmcli", "con", "up", interface])

def cmd_flush_dns(interface: str) -> tuple[bool, str]:
    if SYSTEM == "darwin":
        for cmd in [
            ["networksetup", "-setdnsservers", interface, "empty"],
            ["dscacheutil", "-flushcache"],
            ["killall", "-HUP", "mDNSResponder"],
        ]:
            ok, err = run_elevated(cmd)
            if not ok:
                return False, err
        return True, ""

    elif SYSTEM == "windows":
        return run_elevated(["ipconfig", "/flushdns"])

    else:  # Linux
        for cmd in [
            ["resolvectl", "flush-caches"],
            ["systemd-resolve", "--flush-caches"],
        ]:
            ok, err = run_elevated(cmd)
            if ok:
                return True, ""
        return run_elevated(["systemctl", "restart", "systemd-resolved"])

def cmd_clear_dns(interface: str) -> tuple[bool, str]:
    if SYSTEM == "darwin":
        return run_elevated(["networksetup", "-setdnsservers", interface, "empty"])

    elif SYSTEM == "windows":
        return run_elevated(["netsh", "interface", "ip", "set", "dns", interface, "dhcp"])

    else:  # Linux
        ok, err = run_elevated(["nmcli", "con", "mod", interface, "ipv4.dns", ""])
        if not ok:
            return False, err
        run_elevated(["nmcli", "con", "mod", interface, "ipv4.ignore-auto-dns", "no"])
        return run_elevated(["nmcli", "con", "up", interface])

def cmd_show_dns(interface: str) -> str:
    try:
        if SYSTEM == "darwin":
            r = subprocess.run(
                ["networksetup", "-getdnsservers", interface],
                capture_output=True, text=True
            )
        elif SYSTEM == "windows":
            r = subprocess.run(
                ["netsh", "interface", "ip", "show", "dns", interface],
                capture_output=True, text=True
            )
        else:  # Linux
            r = subprocess.run(
                ["nmcli", "-g", "IP4.DNS", "con", "show", interface],
                capture_output=True, text=True
            )
            if not r.stdout.strip():
                r = subprocess.run(["cat", "/etc/resolv.conf"], capture_output=True, text=True)
        return r.stdout.strip()
    except Exception as e:
        return str(e)

# ================= UI HELPERS =================

def score_color(lat, resp) -> str:
    if lat is None and resp is None:
        return "#444444"
    combined = []
    if lat is not None:
        combined.append(lat)
    if resp is not None:
        combined.append(resp)
    avg = sum(combined) / len(combined)
    if avg < 100:
        return "#4CAF50"
    elif avg <= 200:
        return "#FFB74D"
    else:
        return "#F44336"

def fmt_ms(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:.0f} ms"

def metric_col(label: str, value: str, color: str):
    return ft.Column([
        ft.Text(label, size=9, color="#777777"),
        ft.Text(value, size=12, color=color, weight=ft.FontWeight.BOLD),
    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2)

# ================= MAIN APP =================

async def main(page: ft.Page):
    page.title = "DNS Manager"
    page.bgcolor = "#0A0A0A"
    page.padding = 24
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 820
    page.window.height = 680
    page.window.min_width = 620
    page.window.min_height = 500

    active_dns: str | None = None
    scan_results: list[dict] = []
    custom_dns: dict = load_custom_dns()
    executor = ThreadPoolExecutor(max_workers=len(DNS_SERVERS) + 8)
    _toast_task = None

    loop = asyncio.get_event_loop()
    available_interfaces: list[str] = await loop.run_in_executor(None, get_active_interfaces)
    selected_interface: str | None = available_interfaces[0] if available_interfaces else None

    def get_interface() -> str | None:
        return selected_interface

    iface_dropdown = ft.Dropdown(
        value=selected_interface,
        options=[ft.dropdown.Option(i) for i in available_interfaces] if available_interfaces else [
            ft.dropdown.Option("No active interfaces", disabled=True)
        ],
        disabled=len(available_interfaces) == 0,
        width=210,
        text_size=12,
        border_color="#2A2A2A",
        focused_border_color="#4FC3F7",
        color="#CCCCCC",
        bgcolor="#111111",
        border_radius=8,
        content_padding=ft.padding.symmetric(horizontal=12, vertical=6),
        on_change=lambda e: on_interface_change(e),
    )

    def on_interface_change(e):
        nonlocal selected_interface
        selected_interface = e.control.value

    dns_grid = ft.ResponsiveRow(spacing=10, run_spacing=10)

    # ---- Toast (auto-dismiss) ----
    toast_icon = ft.Icon(ft.icons.CHECK_CIRCLE_ROUNDED, size=16, color="#4CAF50")
    toast_text = ft.Text("", size=12, color="#4CAF50")
    toast_row = ft.Container(
        content=ft.Row([toast_icon, toast_text], spacing=8, tight=True),
        visible=False,
        bgcolor="#1A1A1A",
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=14, vertical=8),
        border=ft.border.all(1, "#2A2A2A"),
    )

    async def show_toast(msg: str, success: bool = True):
        nonlocal _toast_task
        icon_color = "#4CAF50" if success else "#F44336"
        text_color = "#4CAF50" if success else "#F44336"
        toast_icon.name = ft.icons.CHECK_CIRCLE_ROUNDED if success else ft.icons.ERROR_ROUNDED
        toast_icon.color = icon_color
        toast_text.value = msg
        toast_text.color = text_color
        toast_row.border = ft.border.all(1, icon_color + "33")
        toast_row.visible = True
        page.update()

        if _toast_task and not _toast_task.done():
            _toast_task.cancel()

        async def _auto_hide():
            await asyncio.sleep(3)
            toast_row.visible = False
            page.update()

        _toast_task = asyncio.ensure_future(_auto_hide())

    # ---- Status bar ----
    progress_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, color="#4FC3F7")
    status_text = ft.Text("", size=11, color="#4FC3F7")
    scanning_row = ft.Row([progress_ring, status_text], spacing=8, visible=False)
    last_update = ft.Text("", size=11, color="#555555")

    # ---- Show DNS dialog ----
    show_dns_text = ft.Text("", size=12, color="#CCCCCC", selectable=True, font_family="monospace")

    show_dns_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Current DNS Servers", size=14, weight=ft.FontWeight.BOLD, color="#E0E0E0"),
        content=ft.Container(
            content=show_dns_text,
            bgcolor="#111111",
            border_radius=8,
            padding=ft.padding.all(14),
            border=ft.border.all(1, "#2A2A2A"),
            width=320,
        ),
        actions=[
            ft.TextButton(
                "Close",
                on_click=lambda e: close_dialog(),
                style=ft.ButtonStyle(color="#4FC3F7"),
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor="#1A1A1A",
        shape=ft.RoundedRectangleBorder(radius=12),
    )

    def close_dialog():
        show_dns_dialog.open = False
        page.update()

    # ---- Confirm dialog ----
    confirm_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("", size=14, weight=ft.FontWeight.BOLD, color="#E0E0E0"),
        content=ft.Text("", size=13, color="#AAAAAA"),
        bgcolor="#1A1A1A",
        shape=ft.RoundedRectangleBorder(radius=12),
    )

    async def open_confirm(title: str, body: str, on_confirm):
        confirm_dialog.title = ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color="#E0E0E0")
        confirm_dialog.content = ft.Text(body, size=13, color="#AAAAAA")
        confirm_dialog.actions = [
            ft.TextButton(
                "Cancel",
                on_click=lambda e: _close_confirm(),
                style=ft.ButtonStyle(color="#777777"),
            ),
            ft.FilledButton(
                "Confirm",
                on_click=lambda e: page.run_task(_run_confirm, on_confirm),
                style=ft.ButtonStyle(bgcolor="#F44336", color="#FFFFFF"),
            ),
        ]
        confirm_dialog.actions_alignment = ft.MainAxisAlignment.END
        confirm_dialog.open = True
        page.update()

    def _close_confirm():
        confirm_dialog.open = False
        page.update()

    async def _run_confirm(fn):
        _close_confirm()
        await fn()

    # ---- Custom DNS Dialog ----
    custom_name_field = ft.TextField(
        label="Name",
        hint_text="e.g. My Office DNS",
        text_size=13,
        border_color="#2A2A2A",
        focused_border_color="#4FC3F7",
        color="#CCCCCC",
        bgcolor="#0F0F0F",
        border_radius=8,
        label_style=ft.TextStyle(color="#666666", size=11),
    )
    custom_primary_field = ft.TextField(
        label="Primary DNS",
        hint_text="e.g. 1.1.1.1",
        text_size=13,
        border_color="#2A2A2A",
        focused_border_color="#4FC3F7",
        color="#CCCCCC",
        bgcolor="#0F0F0F",
        border_radius=8,
        label_style=ft.TextStyle(color="#666666", size=11),
        keyboard_type=ft.KeyboardType.NUMBER,
    )
    custom_secondary_field = ft.TextField(
        label="Secondary DNS  (optional)",
        hint_text="e.g. 1.0.0.1",
        text_size=13,
        border_color="#2A2A2A",
        focused_border_color="#4FC3F7",
        color="#CCCCCC",
        bgcolor="#0F0F0F",
        border_radius=8,
        label_style=ft.TextStyle(color="#666666", size=11),
        keyboard_type=ft.KeyboardType.NUMBER,
    )
    custom_error_text = ft.Text("", size=11, color="#F44336", visible=False)

    def _validate_custom_fields() -> str | None:
        name = custom_name_field.value.strip()
        primary = custom_primary_field.value.strip()
        secondary = custom_secondary_field.value.strip()
        if not name:
            return "Name is required."
        all_names = {**DNS_SERVERS, **custom_dns}
        if name in all_names:
            return f'"{name}" already exists.'
        if not primary:
            return "Primary DNS is required."
        if not is_valid_ip(primary):
            return f'"{primary}" is not a valid IP address.'
        if secondary and not is_valid_ip(secondary):
            return f'"{secondary}" is not a valid IP address.'
        return None

    def _reset_custom_dialog():
        custom_name_field.value = ""
        custom_primary_field.value = ""
        custom_secondary_field.value = ""
        custom_error_text.value = ""
        custom_error_text.visible = False
        custom_dns_dialog.open = False
        page.update()

    async def _on_add_custom(e):
        err = _validate_custom_fields()
        if err:
            custom_error_text.value = err
            custom_error_text.visible = True
            page.update()
            return

        name = custom_name_field.value.strip()
        primary = custom_primary_field.value.strip()
        secondary = custom_secondary_field.value.strip()
        servers = [primary] + ([secondary] if secondary else [])

        custom_dns[name] = servers
        save_custom_dns(custom_dns)
        _reset_custom_dialog()
        await show_toast(f'"{name}" added — scanning...')
        await _scan_single_custom(name, servers)

    async def _scan_single_custom(name: str, servers: list[str]):
        nonlocal scan_results
        result = await loop.run_in_executor(executor, fetch_metrics, name, servers)
        existing = [r for r in scan_results if r["name"] != name]
        scan_results = existing + [result]
        render_grid()
        page.update()

    custom_dns_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Add Custom DNS", size=14, weight=ft.FontWeight.BOLD, color="#E0E0E0"),
        content=ft.Container(
            content=ft.Column([
                custom_name_field,
                custom_primary_field,
                custom_secondary_field,
                custom_error_text,
            ], spacing=12, tight=True),
            width=320,
            padding=ft.padding.only(top=8),
        ),
        actions=[
            ft.TextButton(
                "Cancel",
                on_click=lambda e: _reset_custom_dialog(),
                style=ft.ButtonStyle(color="#666666"),
            ),
            ft.FilledButton(
                "Add & Scan",
                icon=ft.icons.ADD_ROUNDED,
                on_click=lambda e: page.run_task(_on_add_custom, e),
                style=ft.ButtonStyle(
                    bgcolor="#1A2A3A",
                    color="#4FC3F7",
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor="#1A1A1A",
        shape=ft.RoundedRectangleBorder(radius=12),
    )

    async def on_add_custom_dns(e):
        custom_error_text.value = ""
        custom_error_text.visible = False
        custom_dns_dialog.open = True
        page.update()

    async def _delete_custom(name: str):
        nonlocal scan_results, active_dns
        if name in custom_dns:
            del custom_dns[name]
            save_custom_dns(custom_dns)
        scan_results = [r for r in scan_results if r["name"] != name]
        if active_dns == name:
            active_dns = None
        render_grid()
        await show_toast(f'"{name}" removed')
        page.update()

    # ---- DNS Card ----
    def build_card(result: dict, is_active: bool) -> ft.Container:
        name = result["name"]
        lat = result["lat"]
        resp = result["resp"]
        color = score_color(lat, resp)
        is_custom = name in custom_dns

        border_color = "#4FC3F7" if is_active else color + "55"
        bg = "#141820" if is_active else "#111111"

        lat_color = (
            "#4CAF50" if lat is not None and lat < 100
            else "#FFB74D" if lat is not None and lat <= 200
            else "#F44336" if lat is not None
            else "#555555"
        )
        resp_color = (
            "#4CAF50" if resp is not None and resp < 100
            else "#FFB74D" if resp is not None and resp <= 200
            else "#F44336" if resp is not None
            else "#555555"
        )

        active_badge = ft.Container(
            content=ft.Row([
                ft.Icon(ft.icons.RADIO_BUTTON_CHECKED_ROUNDED, size=9, color="#0D1117"),
                ft.Text("Active", size=9, color="#0D1117", weight=ft.FontWeight.BOLD),
            ], spacing=3, tight=True),
            bgcolor="#4FC3F7",
            border_radius=4,
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            visible=is_active,
        )

        custom_badge = ft.Container(
            content=ft.Text("Custom", size=9, color="#CE93D8", weight=ft.FontWeight.W_500),
            bgcolor="#1E1520",
            border_radius=4,
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            visible=is_custom and not is_active,
        )

        delete_btn = ft.IconButton(
            icon=ft.icons.DELETE_OUTLINE_ROUNDED,
            icon_size=14,
            icon_color="#3A3A3A",
            tooltip="Remove custom DNS",
            on_click=lambda e, n=name: page.run_task(_delete_custom, n),
            visible=is_custom,
            style=ft.ButtonStyle(padding=ft.padding.all(4)),
        )

        activate_btn = ft.FilledButton(
            "Activate",
            icon=ft.icons.POWER_SETTINGS_NEW_ROUNDED,
            on_click=lambda e, n=name, s=result["servers"]: page.run_task(activate_dns, n, s),
            style=ft.ButtonStyle(
                bgcolor="#1A2A3A" if not is_active else "#0D1A2A",
                color="#4FC3F7",
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.padding.symmetric(horizontal=10, vertical=4),
            ),
            visible=not is_active,
            height=30,
        )

        title_row_children = [
            ft.Text(name, size=13, weight=ft.FontWeight.W_600, color="#E0E0E0", expand=True),
            custom_badge,
            active_badge,
        ]
        if is_custom:
            title_row_children.append(delete_btn)

        card_content = ft.Column([
            ft.Row(title_row_children, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Text(", ".join(result["servers"]), size=9, color="#3A3A3A"),
            ft.Container(height=8),
            ft.Row([
                metric_col("Latency", fmt_ms(lat), lat_color),
                ft.Container(width=1, height=28, bgcolor="#1E1E1E"),
                metric_col("Response", fmt_ms(resp), resp_color),
            ], alignment=ft.MainAxisAlignment.SPACE_AROUND),
            ft.Container(height=6),
            ft.Row([activate_btn], alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        card = ft.Container(
            content=card_content,
            padding=ft.padding.all(14),
            border_radius=12,
            bgcolor=bg,
            border=ft.border.all(1, border_color),
            expand=True,
        )

        return ft.Container(
            card,
            col={"xs": 12, "sm": 6, "md": 4, "lg": 3},
            expand=True,
        )

    def render_grid():
        dns_grid.controls.clear()
        for r in scan_results:
            dns_grid.controls.append(build_card(r, r["name"] == active_dns))

    async def activate_dns(name: str, servers: list[str]):
        nonlocal active_dns
        iface = get_interface()
        if not iface:
            await show_toast("No interface selected", success=False)
            return
        await show_toast(f"Activating {name}...")
        ok, err = await loop.run_in_executor(None, cmd_set_dns, iface, servers)
        if ok:
            active_dns = name
            render_grid()
            await show_toast(f"{name} is now active")
        else:
            await show_toast(f"Failed: {err}", success=False)
        page.update()

    async def on_flush(e):
        iface = get_interface()
        if not iface:
            await show_toast("No interface selected", success=False)
            return
        await show_toast("Flushing DNS cache...")
        ok, err = await loop.run_in_executor(None, cmd_flush_dns, iface)
        await show_toast("DNS cache flushed" if ok else f"Failed: {err}", success=ok)
        page.update()

    async def _do_clear():
        nonlocal active_dns
        iface = get_interface()
        if not iface:
            await show_toast("No interface selected", success=False)
            return
        ok, err = await loop.run_in_executor(None, cmd_clear_dns, iface)
        if ok:
            active_dns = None
            render_grid()
        await show_toast("DNS cleared" if ok else f"Failed: {err}", success=ok)
        page.update()

    async def on_clear(e):
        await open_confirm(
            "Clear DNS Settings",
            "This will remove the custom DNS and revert to automatic (DHCP) settings.",
            _do_clear,
        )

    async def on_show(e):
        iface = get_interface()
        if not iface:
            show_dns_text.value = "No interface selected."
            show_dns_dialog.open = True
            page.update()
            return
        result = await loop.run_in_executor(None, cmd_show_dns, iface)
        show_dns_text.value = result
        show_dns_dialog.open = True
        page.update()

    async def run_scan():
        nonlocal scan_results
        progress_ring.visible = True
        scanning_row.visible = True
        all_providers = {**DNS_SERVERS, **custom_dns}
        status_text.value = "Scanning providers..."
        dns_grid.controls.clear()
        for name in all_providers:
            dns_grid.controls.append(
                ft.Container(
                    content=ft.Container(
                        content=ft.Column([
                            ft.Text(name, size=13, weight=ft.FontWeight.W_600, color="#2A2A2A"),
                            ft.Container(height=6),
                            ft.ProgressRing(width=14, height=14, stroke_width=2, color="#2A2A2A"),
                        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                        padding=ft.padding.all(14),
                        border_radius=12,
                        bgcolor="#0D0D0D",
                        border=ft.border.all(1, "#1A1A1A"),
                        expand=True,
                        alignment=ft.alignment.center,
                        height=120,
                    ),
                    col={"xs": 12, "sm": 6, "md": 4, "lg": 3},
                    expand=True,
                )
            )
        page.update()
        await asyncio.sleep(0.05)

        futures = {
            loop.run_in_executor(executor, fetch_metrics, name, servers): name
            for name, servers in all_providers.items()
        }
        results_map = {}
        for fut in asyncio.as_completed(futures.keys()):
            r = await fut
            results_map[r["name"]] = r

        scan_results = [results_map[name] for name in all_providers if name in results_map]
        render_grid()
        last_update.value = f"Updated {datetime.now().strftime('%H:%M')}"
        progress_ring.visible = False
        status_text.value = f"{len(scan_results)} providers"
        page.update()

    async def on_rescan(e):
        page.run_task(run_scan)

    # ---- Action Buttons ----
    flush_btn = ft.OutlinedButton(
        "Flush Cache",
        icon=ft.icons.CLEANING_SERVICES_ROUNDED,
        on_click=on_flush,
        tooltip="Clear the local DNS cache without changing DNS settings",
        style=ft.ButtonStyle(
            color="#FFB74D",
            side=ft.BorderSide(1, "#FFB74D44"),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
        ),
    )
    clear_btn = ft.OutlinedButton(
        "Clear DNS",
        icon=ft.icons.DELETE_OUTLINE_ROUNDED,
        on_click=on_clear,
        tooltip="Remove custom DNS and revert to automatic settings",
        style=ft.ButtonStyle(
            color="#F44336",
            side=ft.BorderSide(1, "#F4433644"),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
        ),
    )
    show_btn = ft.OutlinedButton(
        "Show DNS",
        icon=ft.icons.INFO_OUTLINE_ROUNDED,
        on_click=on_show,
        tooltip="View current DNS servers on selected interface",
        style=ft.ButtonStyle(
            color="#888888",
            side=ft.BorderSide(1, "#33333366"),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
        ),
    )
    rescan_btn = ft.OutlinedButton(
        "Rescan",
        icon=ft.icons.REFRESH_ROUNDED,
        on_click=on_rescan,
        tooltip="Re-test all DNS providers",
        style=ft.ButtonStyle(
            color="#4FC3F7",
            side=ft.BorderSide(1, "#4FC3F744"),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
        ),
    )
    custom_dns_btn = ft.OutlinedButton(
        "Custom DNS",
        icon=ft.icons.ADD_ROUNDED,
        on_click=on_add_custom_dns,
        tooltip="Add a custom DNS provider",
        style=ft.ButtonStyle(
            color="#CE93D8",
            side=ft.BorderSide(1, "#CE93D844"),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
        ),
    )

    # ---- Header ----
    header = ft.Row([
        ft.Column([
            ft.Row([
                ft.Icon(ft.icons.DNS_ROUNDED, color="#4FC3F7", size=24),
                ft.Text("DNS Manager", size=20, weight=ft.FontWeight.BOLD, color="#E0E0E0"),
            ], spacing=10),
            ft.Text("Select a provider and click Activate to apply", size=11, color="#555555"),
        ], spacing=4, expand=True),
        ft.Column([
            ft.Row([
                ft.Text("Interface", size=11, color="#666666"),
                iface_dropdown,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([last_update, scanning_row], spacing=12, alignment=ft.MainAxisAlignment.END),
        ], horizontal_alignment=ft.CrossAxisAlignment.END, spacing=6),
    ], vertical_alignment=ft.CrossAxisAlignment.START)

    action_row = ft.Row(
        [flush_btn, clear_btn, custom_dns_btn, ft.Container(expand=True), show_btn, rescan_btn],
        spacing=8,
    )

    page.add(
        ft.Column([
            header,
            ft.Divider(color="#1A1A1A", height=24),
            action_row,
            ft.Container(height=4),
            toast_row,
            ft.Container(height=6),
            dns_grid,
        ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    )

    page.overlay.append(show_dns_dialog)
    page.overlay.append(confirm_dialog)
    page.overlay.append(custom_dns_dialog)
    page.run_task(run_scan)

if __name__ == "__main__":
    ensure_privileges()
    ft.app(target=main)
