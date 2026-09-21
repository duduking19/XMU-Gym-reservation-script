import os
import re
import yaml
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

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

@dataclass
class SchedulerConfig:
    target_time: str = "07:00:00"
    advance_ms: int = 200
    retry_count: int = 5
    retry_interval_ms: int = 150
    fallback_nearest: bool = True       # 首选时段无名额时是否自动选择最近时段
    pre_check_minutes: int = 5          # 抢票前提前自检并尝试自愈 Session 的分钟数
    weekly_enabled: bool = False
    weekly_plan: Dict[str, str] = field(default_factory=dict)  # 入场日期：1=周一，7=周日
    date_overrides: Dict[str, str] = field(default_factory=dict)  # 特例：入场日期 YYYY-MM-DD -> 时段，优先于计划表

    @staticmethod
    def _check_slot(slot) -> str:
        if not isinstance(slot, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d", slot):
            raise ValueError("计划时段格式应为 HH:MM-HH:MM，例如 16:30-18:00")
        if slot[:5] >= slot[6:]:
            raise ValueError("计划时段结束时间必须晚于开始时间")
        return slot

    def __post_init__(self):
        if not isinstance(self.weekly_enabled, bool) or not isinstance(self.weekly_plan, dict) \
                or not isinstance(self.date_overrides, dict):
            raise ValueError("每周计划格式错误")
        plan = {}
        for day, slot in self.weekly_plan.items():
            if str(day) not in "1 2 3 4 5 6 7".split():
                raise ValueError("每周计划的星期必须是 1（周一）至 7（周日）")
            plan[str(day)] = self._check_slot(slot)
        if self.weekly_enabled and not plan:
            raise ValueError("每周计划至少需要设置一天")
        self.weekly_plan = plan
        overrides = {}
        for day, slot in self.date_overrides.items():
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
                raise ValueError("特例日期格式应为 YYYY-MM-DD")
            overrides[str(day)] = self._check_slot(slot)
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
        data = yaml.safe_load(f) or {}
    
    auth_data = data.get("auth", {})
    target_data = data.get("target", {})
    scheduler_data = data.get("scheduler", {})
    notify_data = data.get("notify", {})

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

    return AppConfig(
        base_url=data.get("base_url", "https://xdty.xmu.edu.cn/bdlp_h5_fitness_test"),
        auth=_build_dataclass(AuthConfig, auth_data),
        target=target_cfg,
        scheduler=_build_dataclass(SchedulerConfig, scheduler_data),
        notify=notify_cfg
    )

def save_phpsessid(config_path: str, new_token: str) -> bool:
    """
    持久化回写新的 PHPSESSID 至配置文件，保留原有注释与缩进
    """
    if not os.path.exists(config_path):
        return False

    import re
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r'(phpsessid:\s*)(["\']?[a-zA-Z0-9_-]*["\']?)'
    if re.search(pattern, content):
        new_content = re.sub(pattern, rf'\g<1>"{new_token}"', content, count=1)
    else:
        data = yaml.safe_load(content) or {}
        if "auth" not in data:
            data["auth"] = {}
        data["auth"]["phpsessid"] = new_token
        new_content = yaml.dump(data, allow_unicode=True, sort_keys=False)

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True

def save_cas_credentials(config_path: str, username: str, password: str) -> bool:
    """持久化统一身份认证账号密码至配置文件（明文，依赖文件权限保护）"""
    if not os.path.exists(config_path):
        return False
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data.get("auth"), dict):
        data["auth"] = {}
    data["auth"]["cas_username"] = username
    data["auth"]["cas_password"] = password
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(yaml.dump(data, allow_unicode=True, sort_keys=False))
    return True

def save_auth_params(config_path: str, params: Dict[str, Any]) -> bool:
    """
    持久化回写 checkLogin 续登参数 auth_params 至配置文件
    """
    if not os.path.exists(config_path):
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    data = yaml.safe_load(content) or {}
    if "auth" not in data or not isinstance(data["auth"], dict):
        data["auth"] = {}
    if "auth_params" not in data["auth"] or not isinstance(data["auth"]["auth_params"], dict):
        data["auth"]["auth_params"] = {}

    data["auth"]["auth_params"].update(params)
    if "uid" in params and params["uid"]:
        data["auth"]["uid"] = str(params["uid"])

    new_content = yaml.dump(data, allow_unicode=True, sort_keys=False)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True

def save_target_and_scheduler_config(
    config_path: str,
    target_updates: Optional[Dict[str, Any]] = None,
    scheduler_updates: Optional[Dict[str, Any]] = None
) -> bool:
    """
    持久化回写用户选择的目标场馆地点、预约时段以及早 7 点抢票定时配置至配置文件
    """
    if not os.path.exists(config_path):
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    data = yaml.safe_load(content) or {}
    if not isinstance(data, dict):
        data = {}

    if target_updates:
        if "target" not in data or not isinstance(data["target"], dict):
            data["target"] = {}
        data["target"].update(target_updates)

    if scheduler_updates:
        if "scheduler" not in data or not isinstance(data["scheduler"], dict):
            data["scheduler"] = {}
        data["scheduler"].update(scheduler_updates)

    # 先验证合并后的计划，非法输入不能覆盖原配置或登录凭据。
    _build_dataclass(SchedulerConfig, data.get("scheduler", {}))
    new_content = yaml.dump(data, allow_unicode=True, sort_keys=False)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True
