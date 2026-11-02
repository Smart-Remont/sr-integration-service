class APIV1PrefixConfig:
    prefix: str = "/v1"
    installment_ff: str = "/installment/ff"
    factoring_ff: str = "/factoring/ff"
    storage: str = "/storage"
    kl_recognition: str = "/kl_recognition"


class APIBigIntegrationPrefixConfig:
    prefix: str = "/big_integration"


class APIKaspiPrefixConfig:
    prefix: str = "/kaspi"


class APISmsPrefixConfig:
    prefix: str = "/sms"


class APIClientSignPrefixConfig:
    prefix: str = "/client_sign"


class APIAutoSignPrefixConfig:
    prefix: str = "/auto_sign"


class APIPrefixConfig:
    prefix: str = "/api"
    v1: APIV1PrefixConfig = APIV1PrefixConfig()
    big_integration: APIBigIntegrationPrefixConfig = APIBigIntegrationPrefixConfig()
    kaspi: APIKaspiPrefixConfig = APIKaspiPrefixConfig()
    sms: APISmsPrefixConfig = APISmsPrefixConfig()
    client_sign: APIClientSignPrefixConfig = APIClientSignPrefixConfig()
    auto_sign: APIAutoSignPrefixConfig = APIAutoSignPrefixConfig()


api_prefix_config = APIPrefixConfig()
