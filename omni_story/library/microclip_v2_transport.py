"""One explicitly resumed known HTTP500, without changing its model input."""
from pathlib import Path

from .pipeline import CodexMCP, _read
from .state import LibraryStopped


class KnownFailureMCP(CodexMCP):
    """Route only the hash-bound failed stage to its single authorized retry."""

    def call(self, name, prompt, media, validator, *, image=False, scope=None):
        permission = self.state.authorization.get('known_failure_resume')
        if permission and name == permission['failed_stage']:
            original = _read(self.output / 'calls' / permission['failed_call_id'] / 'request.json')
            argument = 'image_source' if image else 'video_source'
            if (original['tool'] != ('analyze_image' if image else 'analyze_video')
                    or Path(original['arguments'][argument]).resolve() != Path(media).resolve()
                    or original['observation_scope'] != scope):
                raise LibraryStopped('known_http500_retry_input_changed')
            # Keep the exact paid request's prompt, even after a program reload.
            prompt = original['arguments']['prompt']
            descriptor = _read(self.state.data['artifacts']['mc2_input_' + name][0]['path'])
            dispatch = self.state.authorization.get('known_failure_dispatch_resume')
            name = (dispatch or permission)['retry_stage']
            self.state.set_artifact('mc2_input_' + name, {**descriptor, 'stage': name})
        return super().call(name, prompt, media, validator, image=image, scope=scope)

    def _submit(self, name, request, *, repair_of=None):
        permission = self.state.authorization.get('known_failure_dispatch_resume')
        if not permission or name not in (permission['retry_stage'], permission['retry_stage'] + '_repair'):
            permission = self.state.authorization.get('known_failure_resume')
        if permission and name in (permission['retry_stage'], permission['retry_stage'] + '_repair'):
            # Local request identity only. The official MCP receives the same
            # tool arguments; this field never changes the provider's body.
            request = {**request, 'known_failure_retry_of': permission['failed_call_id']}
        return super()._submit(name, request, repair_of=repair_of)
