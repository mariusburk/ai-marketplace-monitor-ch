import sys
from dataclasses import dataclass, field
from enum import Enum
from itertools import chain
from logging import Logger
from pathlib import Path
from typing import Any, Dict, Generic, List

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from .ai import (
    AnthropicBackend,
    DeepSeekBackend,
    GeminiBackend,
    OllamaBackend,
    OpenAIBackend,
    TAIConfig,
)
from .facebook import FacebookMarketplace
from .marketplace import MarketPlace, TItemConfig, TMarketplaceConfig
from .notification import NotificationConfig
from .region import RegionConfig
from .tutti import TuttiMarketplace
from .user import User, UserConfig
from .utils import MonitorConfig, Translator, hilight, merge_dicts

supported_marketplaces = {"facebook": FacebookMarketplace, "tutti": TuttiMarketplace}


def default_market_type(marketplace_name: str) -> str:
    """Market type assumed when a marketplace section does not specify one.

    A section named after a supported marketplace (``[marketplace.tutti]``)
    defaults to that marketplace; anything else keeps the historical facebook
    default.
    """
    if marketplace_name in supported_marketplaces:
        return marketplace_name
    return MarketPlace.FACEBOOK.value


supported_ai_backends = {
    "deepseek": DeepSeekBackend,
    "gemini": GeminiBackend,
    "openai": OpenAIBackend,
    "anthropic": AnthropicBackend,
    "ollama": OllamaBackend,
}


class IncompleteConfigError(ValueError):
    """A config that parses and validates, but has nothing to run yet.

    A monitor needs a marketplace, a recipient and something to hunt for. Set
    up through the web UI those arrive one at a time, so the file is briefly
    valid-but-not-yet-runnable. The monitor waits for it and says so
    (`MarketplaceMonitor.load_config_file`); the UI has to be able to save it
    in the meantime, which it cannot do if this is indistinguishable from a
    typo.
    """


class ConfigItem(Enum):
    MONITOR = "monitor"
    MARKETPLACE = "marketplace"
    USER = "user"
    ITEM = "item"
    AI = "ai"
    REGION = "region"
    NOTIFICATION = "notification"
    TRANSLATION = "translation"


@dataclass
class Config(Generic[TAIConfig, TItemConfig, TMarketplaceConfig]):
    monitor: MonitorConfig = field(init=False)
    ai: Dict[str, TAIConfig] = field(init=False)
    user: Dict[str, UserConfig] = field(init=False)
    notification: Dict[str, NotificationConfig] = field(init=False)
    marketplace: Dict[str, TMarketplaceConfig] = field(init=False)
    item: Dict[str, TItemConfig] = field(init=False)
    translator: Dict[str, Translator] = field(init=False)
    region: Dict[str, RegionConfig] = field(init=False)

    def __init__(self: "Config", config_files: List[Path], logger: Logger | None = None) -> None:
        configs = []
        system_config = Path(__file__).parent / "config.toml"

        for config_file in [system_config, *config_files]:
            try:
                if logger:
                    logger.debug(
                        f"""{hilight("[Monitor]", "succ")} config file {hilight(str(config_file))}"""
                    )
                with open(config_file, "rb") as f:
                    configs.append(tomllib.load(f))
            except tomllib.TOMLDecodeError as e:
                raise ValueError(f"Error parsing config file {config_file}: {e}") from e
        #
        # merge the list of configs into a single dictionary, including dictionaries in the values
        config = merge_dicts(configs)

        self.validate_sections(config)
        self.get_translator_config(config)
        self.get_monitor_config(config)
        self.get_ai_config(config)
        self.get_notification_config(config)
        self.get_marketplace_config(config)
        self.get_user_config(config)
        self.get_region_config(config)
        self.get_item_config(config)
        self.validate_users()
        self.validate_ais()
        self.expand_notifications(logger)
        self.expand_regions()
        self.validate_items()

    def get_translator_config(self: "Config", config: Dict[str, Any]) -> None:
        if not isinstance(config.get("translation", {}), dict):
            raise ValueError("translation section must be a dictionary.")

        self.translator = {}
        for key, value in config.get("translation", {}).items():
            if "locale" not in value:
                raise ValueError(f"Translation section {hilight(key)} must contain a locale.")
            self.translator[key] = Translator(
                locale=value["locale"],
                dictionary={k: v for k, v in value.items() if k != "locale"},
            )

    def get_monitor_config(self: "Config", config: Dict[str, Any]) -> None:
        self.monitor = MonitorConfig(name="monitor", **config.get("monitor", {}))

    def get_ai_config(self: "Config", config: Dict[str, Any]) -> None:
        # convert ai config to AIConfig objects
        if not isinstance(config.get("ai", {}), dict):
            raise ValueError("ai section must be a dictionary.")

        self.ai = {}
        for key, value in config.get("ai", {}).items():
            try:
                backend_class = supported_ai_backends[value.get("provider", key).lower()]
            except KeyboardInterrupt:
                raise
            except Exception as e:
                raise ValueError(
                    f"Config file contains an unsupported AI backend {key} in the ai section."
                ) from e
            self.ai[key] = backend_class.get_config(name=key, **value)

    def get_notification_config(self: "Config", config: Dict[str, Any]) -> None:
        if not isinstance(config.get("notification", {}), dict):
            raise ValueError("notification section must be a dictionary.")

        self.notification: Dict[str, NotificationConfig] = {}
        for key, value in config.get("notification", {}).items():
            cfg = NotificationConfig.get_config(name=key, **value)
            if cfg is None:
                raise ValueError(
                    f"Unable to determine notification type for notification section {key}"
                )
            else:
                self.notification[key] = cfg

    def get_marketplace_config(self: "Config", config: Dict[str, Any]) -> None:
        # check for required fields in each marketplace
        self.marketplace = {}
        for marketplace_name, marketplace_config in config["marketplace"].items():
            market_type = marketplace_config.get(
                "market_type", default_market_type(marketplace_name)
            )
            if market_type not in supported_marketplaces:
                raise ValueError(
                    f"Marketplace {hilight(market_type)} is not supported. Supported marketplaces are: {supported_marketplaces.keys()}"
                )
            marketplace_class = supported_marketplaces[market_type]
            self.marketplace[marketplace_name] = marketplace_class.get_config(
                name=marketplace_name,
                monitor_config=self.monitor,
                **{**marketplace_config, "market_type": market_type},
            )
            lan = self.marketplace[marketplace_name].language
            if lan is None:
                continue
            # no exact match is required
            if lan.split("_")[0] not in {
                x.split("_")[0] for x in config[ConfigItem.TRANSLATION.value].keys()
            }:
                raise ValueError(f"Translation for language {lan} is not supported.")

    def get_user_config(self: "Config", config: Dict[str, Any]) -> None:
        # check for required fields in each user
        self.user: Dict[str, UserConfig] = {}
        for user_name, user_config in config["user"].items():
            self.user[user_name] = User.get_config(name=user_name, **user_config)

    def get_region_config(self: "Config", config: Dict[str, Any]) -> None:
        # check for required fields in each user
        self.region: Dict[str, RegionConfig] = {}
        for region_name, region_config in config.get("region", {}).items():
            self.region[region_name] = RegionConfig(name=region_name, **region_config)

    def get_item_config(self: "Config", config: Dict[str, Any]) -> None:
        # check for required fields in each user

        self.item = {}
        for item_name, item_config in config["item"].items():
            targets = self.item_marketplaces(item_name, item_config, config["marketplace"])

            for marketplace_name in targets:
                marketplace_class = supported_marketplaces[
                    config["marketplace"][marketplace_name].get(
                        "market_type", default_market_type(marketplace_name)
                    )
                ]
                # One authored item becomes one runtime config per marketplace,
                # each validated by that marketplace's own class. `.name` stays
                # the authored name, so counters, notifications and log lines
                # keep talking about one hunt; only the dict key is suffixed,
                # and only when there is more than one target.
                key = item_name if len(targets) == 1 else f"{item_name}@{marketplace_name}"
                self.item[key] = marketplace_class.get_item_config(
                    name=item_name,
                    marketplace=marketplace_name,
                    **{x: y for x, y in item_config.items() if x != "marketplace"},
                )

    def find_item(self: "Config", name: str) -> TItemConfig | None:
        """Look an item up by its key or by its authored name.

        An item searched on several marketplaces is stored once per
        marketplace under a suffixed key, so a plain name still has to resolve
        — that is what a person types on the command line.
        """
        if name in self.item:
            return self.item[name]
        for item in self.item.values():
            if item.name == name:
                return item
        return None

    def item_names(self: "Config") -> List[str]:
        """The authored names, each listed once however many marketplaces it runs on."""
        names: List[str] = []
        for item in self.item.values():
            if item.name not in names:
                names.append(item.name)
        return names

    @staticmethod
    def item_marketplaces(
        item_name: str, item_config: Dict[str, Any], marketplaces: Dict[str, Any]
    ) -> List[str]:
        """Which marketplaces an item should be searched on.

        A string names one, a list names several, and an absent value keeps the
        historical behaviour of using the first marketplace defined — not all of
        them, which would silently double the searches of existing configs.
        """
        declared = item_config.get("marketplace")
        if declared is None:
            return list(marketplaces)[:1]
        names = [declared] if isinstance(declared, str) else list(declared)
        if not names:
            raise ValueError(f"Item {hilight(item_name)} lists no marketplace to search.")
        for name in names:
            if not isinstance(name, str) or name not in marketplaces:
                raise ValueError(
                    f"Item {hilight(item_name)} specifies a marketplace that does not exist: {name}"
                )
        seen: List[str] = []
        for name in names:
            if name not in seen:
                seen.append(name)
        return seen

    def validate_sections(self: "Config", config: Dict[str, Any]) -> None:
        # check for required sections
        # An invalid section is a mistake and is reported first; a missing one
        # is merely a config that is not finished being written.
        for key in config:
            if key not in [x.value for x in ConfigItem]:
                raise ValueError(f"Config file contains an invalid section {key}.")

        for required_section in ["marketplace", "user", "item"]:
            if required_section not in config:
                raise IncompleteConfigError(
                    f"Config file does not contain a {required_section} section."
                )

    def validate_users(self: "Config") -> None:
        """Check if notified users exists"""
        # if user is specified in other section, they must exist
        for config in chain(self.marketplace.values(), self.item.values()):
            for user in config.notify or []:
                if user not in self.user:
                    raise ValueError(
                        f"User {hilight(user)} specified in {hilight(config.name)} does not exist."
                    )

    def validate_ais(self: "Config") -> None:
        # if ai is specified in other section, they must exist
        for config in chain(self.marketplace.values(), self.item.values()):
            for ai in config.ai or []:
                if ai not in self.ai:
                    raise ValueError(
                        f"AI {hilight(config.ai)} specified in {hilight(config.name)} does not exist."
                    )

    def expand_notifications(self: "Config", logger: Logger | None = None) -> None:
        for config in self.user.values():
            for notification_name in (
                config.notify_with if config.notify_with is not None else self.notification.keys()
            ):
                notification_types = set()
                if notification_name not in self.notification:
                    raise ValueError(
                        f"User {hilight(config.name)} specifies an undefined notification method {notification_name}."
                    )
                notification_config = self.notification[notification_name]
                #
                if notification_config.enabled is False:
                    continue
                # add values of notification_config to user config
                if notification_config.__class__.__name__ in notification_types:
                    if logger:
                        logger.warning(
                            f"Ignore additional notification {hilight(notification_name)} with type {notification_config.__class__.__name__} for user {config.name}."
                        )
                    continue
                else:
                    notification_types.add(notification_config.__class__.__name__)

                for key, value in notification_config.__dict__.items():
                    # name is the notification name and should not override username
                    if key not in ("type", "name") and value is not None:
                        if getattr(config, key) is not None:
                            if logger:
                                logger.warning(
                                    f"Overriding {hilight(key)} for user {config.name} with value {value} from notification {hilight(notification_name)}."
                                )
                        setattr(config, key, value)

    def expand_regions(self: "Config") -> None:
        # if region is specified in other section, they must exist
        for config in chain(self.marketplace.values(), self.item.values()):
            if config.search_region is None:
                continue
            config.city_name = []
            config.search_city = []
            config.radius = []
            config.currency = []

            for region in config.search_region:
                region_config: RegionConfig = self.region[region]
                if region not in self.region:
                    raise ValueError(
                        f"Region {hilight(region)} specified in {hilight(config.name)} does not exist."
                    )
                if region_config.enabled is False:
                    continue
                # avoid duplicated addition of search_city
                for search_city, city_name, radius, currency in zip(
                    region_config.search_city or [],
                    region_config.city_name or [],
                    region_config.radius or [],
                    region_config.currency or [],
                ):
                    if search_city not in config.search_city:
                        config.search_city.append(search_city)
                        config.city_name.append(city_name)
                        config.radius.append(radius)
                        config.currency.append(currency)

    def validate_items(self: "Config") -> None:
        # if item is specified in other section, they must exist
        for marketplace_config in self.marketplace.values():
            if marketplace_config.enabled is False:
                continue
            for item_config in self.item.values():
                if item_config.enabled is False:
                    continue
                if (
                    item_config.marketplace is None
                    or item_config.marketplace == marketplace_config.name
                ):
                    marketplace_class = supported_marketplaces[
                        marketplace_config.market_type
                        or default_market_type(marketplace_config.name)
                    ]
                    if not marketplace_class.requires_search_city:
                        continue
                    if not item_config.search_city and not marketplace_config.search_city:
                        raise ValueError(
                            f"No search_city or search_region is specified for {item_config.name} or market {marketplace_config.name}"
                        )
