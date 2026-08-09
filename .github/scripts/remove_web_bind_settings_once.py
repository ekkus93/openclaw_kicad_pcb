from pathlib import Path

path = Path("src/kicad_pcb_web/settings.py")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        '''_WEB_CONFIG_KEYS: frozenset[str] = frozenset(
    {"data_dir", "default_host", "default_port", "mutation_lock_timeout_s"}
)
''',
        '''_WEB_CONFIG_KEYS: frozenset[str] = frozenset({"data_dir", "mutation_lock_timeout_s"})
''',
    ),
    (
        '_REMOVED_NETWORK_PROBE_ENV = "KICAD_PCB_WEB_LLM_NETWORK_PROBE_ENABLED"\n',
        '_REMOVED_NETWORK_PROBE_ENV = "KICAD_PCB_WEB_LLM_NETWORK_PROBE_ENABLED"\n'
        '_REMOVED_WEB_BIND_ENVS: frozenset[str] = frozenset(\n'
        '    {"KICAD_PCB_WEB_HOST", "KICAD_PCB_WEB_PORT"}\n'
        ')\n',
    ),
    (
        '    default_host: str = "127.0.0.1"\n    default_port: int = 8000\n',
        '',
    ),
    (
        '    config_file = _load_config_file()\n',
        '    for removed_env in _REMOVED_WEB_BIND_ENVS:\n'
        '        if removed_env in os.environ:\n'
        '            raise ValueError(\n'
        '                f"{removed_env} has been removed; pass host/port to the ASGI server"\n'
        '            )\n'
        '\n'
        '    config_file = _load_config_file()\n',
    ),
    (
        '''    default_host = _read_setting(
        env_name="KICAD_PCB_WEB_HOST",
        config_value=web_config.get("default_host"),
        default="127.0.0.1",
    )
    default_port_raw = _read_setting(
        env_name="KICAD_PCB_WEB_PORT",
        config_value=web_config.get("default_port"),
        default=8000,
    )
''',
        '',
    ),
    (
        '''    default_port = _coerce_int(default_port_raw, field_name="web.default_port")
    if not 1 <= default_port <= 65535:
        raise ValueError("web.default_port must be between 1 and 65535")

''',
        '',
    ),
    (
        '        default_host=str(default_host),\n        default_port=default_port,\n',
        '',
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"Expected settings block not found: {old[:80]!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
