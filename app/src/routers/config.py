class APIV1PrefixConfig:
    prefix: str = "/v1"
    installment_ff: str = "/installment/ff"
    factoring_ff: str = "/factoring/ff"
    storage: str = "/storage"
    kl_recognition: str = "/kl_recognition"


class APIBigIntegrationPrefixConfig:
    prefix: str = "/big_integration"


class APIBiBadgePrefixConfig:
    prefix: str = "/bi-badge"


class APIPrefixConfig:
    prefix: str = "/api"
    v1: APIV1PrefixConfig = APIV1PrefixConfig()
    big_integration: APIBigIntegrationPrefixConfig = APIBigIntegrationPrefixConfig()
    bi_badge: APIBiBadgePrefixConfig = APIBiBadgePrefixConfig()


api_prefix_config = APIPrefixConfig()
