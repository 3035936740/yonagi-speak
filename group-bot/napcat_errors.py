"""NapCat business errors are distinct from uncertain HTTP delivery failures."""
import re


class NapCatAPIError(RuntimeError):
    def __init__(self, action, result):
        from debug_events import safe_log
        self.action = action
        self.retcode = result.get('retcode')
        self.status = result.get('status')
        self.detail = safe_log(result.get('message') or result.get('wording') or '未提供错误说明')[:300]
        super().__init__(f'NapCat API failed: {action}; retcode={self.retcode}; {self.detail}')

    @property
    def missing_forward_source(self):
        return self.action == 'forward_group_single_msg' and bool(re.search(
            r'无法找到消息|消息不存在|(?:message|msg).*(?:not found|does not exist)|'
            r'(?:action|api).*(?:not found|not supported)|不支持.*(?:接口|操作)', self.detail, re.I))

    @property
    def retryable(self):
        # A timeout may mean the message was accepted. Do not blindly send it again.
        return not re.search(r'timeout|timed out|超时', self.detail, re.I) and bool(re.search(
            r'繁忙|频率|限流|稍后重试|rate.?limit|too many requests|busy', self.detail, re.I))
