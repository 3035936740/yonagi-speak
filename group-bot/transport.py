def service_tier_args(cfg):
    return ['-c', 'service_tier="fast"' if cfg.get('fast_mode', False) else 'service_tier="default"']


def text_transport_args(cfg):
    tier = service_tier_args(cfg)
    if cfg.get('text_transport', 'http') != 'http':
        return tier
    # Official CLI handles saved OpenAI login; no tokens or endpoint overrides.
    return tier + ['-c', 'model_provider="qq-http"',
        '-c', 'model_providers.qq-http.name="OpenAI HTTP"',
        '-c', 'model_providers.qq-http.wire_api="responses"',
        '-c', 'model_providers.qq-http.requires_openai_auth=true',
        '-c', 'model_providers.qq-http.supports_websockets=false']
