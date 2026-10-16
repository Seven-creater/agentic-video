"""Check four installed CLI entry points and live packaged resources offline."""
from pathlib import Path
import subprocess
import sys
from omni_story.library.resources.original_rough_v1 import provenance
from omni_story.library import story_finecut

provenance()
resources = Path(story_finecut.__file__).parent / 'resources'
for name in ('visual_story_finecut/SKILL.md', 'visual_story_finecut/references/decision-cards.md',
             'visual_audio/decision-cards.md'):
    if not (resources / name).is_file():
        raise RuntimeError('Missing packaged resource: ' + name)
for module in ('hot_pipeline', 'server_cli', 'visual_indexer', 'visual_audio'):
    subprocess.run([sys.executable, '-m', 'omni_story.library.' + module, '--help'],
                   check=True, capture_output=True, timeout=30)
print('Current package resources and four CLI help checks passed; no model calls.')
