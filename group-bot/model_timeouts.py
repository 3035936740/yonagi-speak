"""Independent stage budgets; mixed model requests add the stages they use."""
STAGES={'chat':('model_timeout_seconds',30),'vision':('vision_timeout_seconds',60),'image':('image_timeout_seconds',150)}


def stage_timeout(cfg,stage):
    key,default=STAGES[stage]
    return float(cfg.get(key,default))


def combined_timeout(cfg,*,chat=False,vision=False,image=False):
    return sum(stage_timeout(cfg,stage) for stage,enabled in [('chat',chat),('vision',vision),('image',image)] if enabled)


def vision_config(cfg):
    timeout=stage_timeout(cfg,'vision')
    return {**cfg,'model_timeout_seconds':timeout,'_request_timeout_seconds':timeout}


def request_timeout(cfg,images=None):
    # Explicit pure-vision and planner budgets are already stage-specific.
    return cfg.get('_request_timeout_seconds',combined_timeout(cfg,chat=True,vision=bool(images)))
