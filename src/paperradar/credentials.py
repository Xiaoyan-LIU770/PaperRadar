"""仅访问PaperRadar专用macOS钥匙串项；不把凭证放入argv或项目文件。"""
import json
import os
import shlex
import subprocess
from pathlib import Path

from .core import ROOT

SERVICE = "PaperRadar.OpenAlex"
ACCOUNT = "PaperRadar"
POLICY = ROOT / "config/credential_policy.json"


def load_openalex_key():
    key = os.environ.get("OPENALEX_API_KEY")
    if key: return key, "environment"
    if not POLICY.exists() or not json.loads(POLICY.read_text()).get("keychain_enabled"):
        return None, "not_configured"
    try:
        result = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"], capture_output=True, text=True, timeout=12)
    except subprocess.TimeoutExpired:
        return None, "keychain_timeout_or_interaction_required"
    if result.returncode:
        return None, "keychain_unavailable_" + str(result.returncode)
    return result.stdout.strip() or None, "macos_keychain"


def store_openalex_key(key):
    if not key or any(c.isspace() for c in key): raise ValueError("Key格式不符合要求")
    # security -i从stdin读取命令；密钥不出现在进程参数。捕获内容全部丢弃，只检查退出码。
    command = "add-generic-password -U -a " + ACCOUNT + " -s " + SERVICE + " -T /usr/bin/security -w " + shlex.quote(key) + "\n"
    result = subprocess.run(["/usr/bin/security", "-i"], input=command, capture_output=True, text=True, timeout=30)
    # 交互模式的总退出码可能仍为0，须实际读回比较，不能把执行尝试当作保存成功。
    check = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"], capture_output=True, text=True, timeout=12)
    if result.returncode or check.returncode or check.stdout.strip() != key:
        raise RuntimeError("钥匙串保存未通过读回验证；未创建启用配置")
    from .core import dump, now
    dump(POLICY, {"keychain_enabled": True, "service": SERVICE, "account": ACCOUNT, "authorized_at": now(), "secret_in_project": False})
