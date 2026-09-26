import os
import re
import json
import tempfile
import threading
import yaml
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import ClassVar, Optional, List, Dict, Any, Callable

_CONFIG_LOCK = threading.RLock()

@contextmanager
def _locked_config(path: str):
    """同一配置的读改写跨线程、跨进程串行化。"""
    with _CONFIG_LOCK:
        fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                os.write(fd, b"0") if os.fstat(fd).st_size == 0 else None
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

def _write_private(path: str, content: str):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(prefix=".config-", dir=directory, text=True)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

def _update_config(path: str, edit: Callable[[str], str]) -> bool:
    with _locked_config(path):
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        _write_private(path, edit(content))
        return True

def ensure_config_file(path: str, example_path: str) -> str:
    with _locked_config(path):
        if not os.path.exists(path):
            with open(example_path, "r", encoding="utf-8") as f:
                _write_private(path, f.read())
    return path

@dataclass
class AuthConfig:
    phpsessid: str = ""
    uid: str = ""
    heartbeat_interval_seconds: int = 300
    auto_harvest_enabled: bool = True
    wechat_appid: str = "wx81a2b2fa90759cb7"
    wechat_appex_path: str = ""
    auth_params: Dict[str, Any] = field(default_factory=dict)
    cas_username: str = ""  # 统一身份认证账号密码，用于 checkLogin 失效后的自动重新登录
    cas_password: str = ""

    def __post_init__(self):
        if self.auth_params is None:
            self.auth_params = {}
        if not isinstance(self.auth_params, dict):
            raise ValueError("auth.auth_params 必须是映射")
        if self.phpsessid is None:
            self.phpsessid = ""
        if not isinstance(self.phpsessid, str):
            raise ValueError("auth.phpsessid 必须是字符串")

@dataclass
class TargetConfig:
    stadium_id: int = 16
    venue_id: int = 14
    category_id: int = 8
    stadium_name: str = "翔安校区健身房"
    project_name: str = "健身房"
    area_name: str = "爱秋体育馆健身房"
    area_id: int = 67
    preferred_time: str = "19:30-21:00"
    target_date_offset: int = 1  # 0 为今天，1 为明天
    user_range: str = "[67]"

    def __post_init__(self):
        if type(self.target_date_offset) is not int or self.target_date_offset not in (0, 1):
            raise ValueError("预约日期偏移量只能是 0 或 1")
        SchedulerConfig._check_slot(self.preferred_time)

@dataclass
class SchedulerConfig:
    target_time: str = "07:00:00"
    advance_ms: int = 200
    retry_count: int = 5
    retry_interval_ms: int = 150
    fallback_nearest: bool = True       # 首选时段无名额时是否自动选择最近时段
    pre_check_minutes: int = 5          # 抢票前提前自检并尝试自愈 Session 的分钟数
    release_grace_seconds: int = 600    # 准点后放票并非瞬时完成：时段缺失/仍锁定时持续轮询的秒数
    weekly_enabled: bool = False
    # 每天可填最多 MAX_SLOTS 个时段，按优先级从高到低；高优先级约不到时自动尝试下一个
    weekly_plan: Dict[str, List[str]] = field(default_factory=dict)  # 入场日期：1=周一，7=周日
    date_overrides: Dict[str, List[str]] = field(default_factory=dict)  # 特例：入场日期 YYYY-MM-DD -> 时段，优先于计划表

    MAX_SLOTS: ClassVar[int] = 3

    @staticmethod
    def _check_slot(slot) -> str:
        if not isinstance(slot, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", slot):
            raise ValueError("计划时段格式应为 HH:MM-HH:MM，例如 16:30-18:00")
        if slot[:5] >= slot[6:]:
            raise ValueError("计划时段结束时间必须晚于开始时间")
        return slot

    @classmethod
    def _check_slots(cls, value) -> List[str]:
        """按优先级从高到低的时段列表；旧配置里的单个字符串按一个时段处理"""
        if value in (None, "", []):
            return []
        slots = [value] if isinstance(value, str) else value
        if not isinstance(slots, list) or len(slots) > cls.MAX_SLOTS:
            raise ValueError(f"每天最多设置 {cls.MAX_SLOTS} 个优先级时段")
        slots = [cls._check_slot(s) for s in slots]
        if len(set(slots)) != len(slots):
            raise ValueError("同一天的优先级时段不能重复")
        return slots

    def __post_init__(self):
        if not isinstance(self.target_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", self.target_time):
            raise ValueError("定时预约时间格式应为 HH:MM:SS")
        for name in ("retry_count", "retry_interval_ms", "pre_check_minutes", "release_grace_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"scheduler.{name} 必须是非负整数")
        if not 1 <= self.retry_count <= 5:
            raise ValueError("scheduler.retry_count 必须在 1 到 5 之间")
        if not 100 <= self.retry_interval_ms <= 10000:
            raise ValueError("scheduler.retry_interval_ms 必须在 100 到 10000 毫秒之间")
        if not 1 <= self.pre_check_minutes <= 60:
            raise ValueError("scheduler.pre_check_minutes 必须在 1 到 60 分钟之间")
        if not 0 <= self.release_grace_seconds <= 600:
            raise ValueError("scheduler.release_grace_seconds 必须在 0 到 600 秒之间")
        if type(self.advance_ms) is not int or not 0 <= self.advance_ms <= 1000:
            raise ValueError("scheduler.advance_ms 必须在 0 到 1000 毫秒之间")
        if not isinstance(self.weekly_enabled, bool) or not isinstance(self.weekly_plan, dict) \
                or not isinstance(self.date_overrides, dict):
            raise ValueError("每周计划格式错误")
        plan = {}
        for day, slots in self.weekly_plan.items():
            if str(day) not in "1 2 3 4 5 6 7".split():
                raise ValueError("每周计划的星期必须是 1（周一）至 7（周日）")
            slots = self._check_slots(slots)
            if slots:
                plan[str(day)] = slots
        if self.weekly_enabled and not plan:
            raise ValueError("每周计划至少需要设置一天")
        self.weekly_plan = plan
        overrides = {}
        for day, slots in self.date_overrides.items():
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
                raise ValueError("特例日期格式应为 YYYY-MM-DD")
            slots = self._check_slots(slots)
            if slots:
                overrides[str(day)] = slots
        self.date_overrides = overrides

@dataclass
class EmailConfig:
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    ssl: bool = True
    sender: str = ""
    password: str = ""
    to_addrs: List[str] = field(default_factory=list)

@dataclass
class PushPlusConfig:
    token: str = ""

@dataclass
class ServerChanConfig:
    sendkey: str = ""

@dataclass
class BarkConfig:
    server_url: str = "https://api.day.app"
    device_key: str = ""

@dataclass
class FeishuConfig:
    webhook_url: str = ""
    secret: str = ""  # 机器人开启签名校验时填写

@dataclass
class NotifyConfig:
    enabled: bool = False
    channel: str = "pushplus"  # email, pushplus, serverchan, bark, feishu, all
    title_prefix: str = "【厦大体育馆预约】"
    email: EmailConfig = field(default_factory=EmailConfig)
    pushplus: PushPlusConfig = field(default_factory=PushPlusConfig)
    serverchan: ServerChanConfig = field(default_factory=ServerChanConfig)
    bark: BarkConfig = field(default_factory=BarkConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)

@dataclass
class AppConfig:
    base_url: str = "https://xdty.xmu.edu.cn/bdlp_h5_fitness_test"
    auth: AuthConfig = field(default_factory=AuthConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

def _build_dataclass(cls, data: Optional[Dict[str, Any]]):
    if not isinstance(data, dict):
        return cls()
    field_names = {f for f in cls.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in field_names}
    return cls(**filtered)

def load_config(config_path: str = "config/config.yaml") -> AppConfig:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是映射")
    
    auth_data = data.get("auth") or {}
    target_data = data.get("target") or {}
    scheduler_data = data.get("scheduler") or {}
    notify_data = data.get("notify") or {}
    if any(not isinstance(section, dict) for section in (auth_data, target_data, scheduler_data, notify_data)):
        raise ValueError("配置分区必须是映射")

    # 递归构建 NotifyConfig (支持 pushplus_token 极简单行写法)
    pushplus_raw = notify_data.get("pushplus")
    if not isinstance(pushplus_raw, dict):
        token = notify_data.get("pushplus_token") or notify_data.get("token")
        if token:
            pushplus_raw = {"token": str(token).strip()}
    email_cfg = _build_dataclass(EmailConfig, notify_data.get("email"))
    pushplus_cfg = _build_dataclass(PushPlusConfig, pushplus_raw)
    serverchan_cfg = _build_dataclass(ServerChanConfig, notify_data.get("serverchan"))
    bark_cfg = _build_dataclass(BarkConfig, notify_data.get("bark"))
    feishu_cfg = _build_dataclass(FeishuConfig, notify_data.get("feishu"))

    notify_field_names = {f for f in NotifyConfig.__dataclass_fields__}
    filtered_notify = {k: v for k, v in notify_data.items() if k in notify_field_names and k not in ("email", "pushplus", "serverchan", "bark", "feishu")}
    
    # 若用户未显式配置 enabled，但填写了任意推送 token，则自动启用通知
    if "enabled" not in filtered_notify:
        has_any_token = bool(
            pushplus_cfg.token or
            email_cfg.sender or
            serverchan_cfg.sendkey or
            bark_cfg.device_key or
            feishu_cfg.webhook_url
        )
        filtered_notify["enabled"] = has_any_token

    notify_cfg = NotifyConfig(
        email=email_cfg,
        pushplus=pushplus_cfg,
        serverchan=serverchan_cfg,
        bark=bark_cfg,
        feishu=feishu_cfg,
        **filtered_notify
    )

    target_cfg = _build_dataclass(TargetConfig, target_data)
    # 智能自适应校区：若指定了思明校区 (stadium_id=6) 且未显式指定 area_id，自动对齐思明校区参数
    if target_cfg.stadium_id == 6 and "area_id" not in target_data:
        if target_cfg.stadium_name == "翔安校区健身房":
            target_cfg.stadium_name = "思明校区健身房"
        if target_cfg.area_name == "爱秋体育馆健身房":
            target_cfg.area_name = "思明校区健身房"
        target_cfg.area_id = 0
        target_cfg.user_range = "[]"
        if "venue_id" not in target_data:
            target_cfg.venue_id = 6

    base_url = data.get("base_url") or AppConfig.base_url
    if not isinstance(base_url, str):
        raise ValueError("base_url 必须是字符串")
    return AppConfig(
        base_url=base_url,
        auth=_build_dataclass(AuthConfig, auth_data),
        target=target_cfg,
        scheduler=_build_dataclass(SchedulerConfig, scheduler_data),
        notify=notify_cfg
    )

def save_phpsessid(config_path: str, new_token: str) -> bool:
    """
    持久化回写新的 PHPSESSID 至配置文件，保留原有注释与缩进
    """
    if not isinstance(new_token, str) or not new_token:
        raise ValueError("PHPSESSID 不能为空")
    def edit(content: str) -> str:
        pattern = r'(?m)^(\s*phpsessid:[ \t]*)([^\r\n]*)'
        if re.search(pattern, content):
            return re.sub(pattern, lambda m: m.group(1) + json.dumps(new_token), content, count=1)
        data = yaml.safe_load(content) or {}
        data.setdefault("auth", {})["phpsessid"] = new_token
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return _update_config(config_path, edit)

def save_cas_credentials(config_path: str, username: str, password: str) -> bool:
    """持久化统一身份认证账号密码至配置文件（明文，依赖文件权限保护）"""
    def edit(content: str) -> str:
        data = yaml.safe_load(content) or {}
        if not isinstance(data.get("auth"), dict):
            data["auth"] = {}
        data["auth"]["cas_username"] = username
        data["auth"]["cas_password"] = password
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return _update_config(config_path, edit)

def save_auth_params(config_path: str, params: Dict[str, Any]) -> bool:
    """
    持久化回写 checkLogin 续登参数 auth_params 至配置文件
    """
    def edit(content: str) -> str:
        data = yaml.safe_load(content) or {}
        if not isinstance(data.get("auth"), dict):
            data["auth"] = {}
        if not isinstance(data["auth"].get("auth_params"), dict):
            data["auth"]["auth_params"] = {}
        data["auth"]["auth_params"].update(params)
        if params.get("uid"):
            data["auth"]["uid"] = str(params["uid"])
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return _update_config(config_path, edit)

def save_target_and_scheduler_config(
    config_path: str,
    target_updates: Optional[Dict[str, Any]] = None,
    scheduler_updates: Optional[Dict[str, Any]] = None
) -> bool:
    """
    持久化回写用户选择的目标场馆地点、预约时段以及早 7 点抢票定时配置至配置文件
    """
    if target_updates is not None and not isinstance(target_updates, dict) or scheduler_updates is not None and not isinstance(scheduler_updates, dict):
        raise ValueError("目标与定时配置必须是映射")
    def edit(content: str) -> str:
        data = yaml.safe_load(content) or {}
        if not isinstance(data, dict):
            raise ValueError("配置文件顶层必须是映射")
        if target_updates:
            if not isinstance(data.get("target"), dict):
                data["target"] = {}
            data["target"].update(target_updates)
        if scheduler_updates:
            if not isinstance(data.get("scheduler"), dict):
                data["scheduler"] = {}
            data["scheduler"].update(scheduler_updates)
        _build_dataclass(TargetConfig, data.get("target"))
        _build_dataclass(SchedulerConfig, data.get("scheduler"))
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return _update_config(config_path, edit)
