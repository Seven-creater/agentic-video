"""Run the separately pinned music production candidate without gold labels."""
from pathlib import Path
import argparse
import hashlib
import json
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();job=json.loads(args.run.read_text());candidate=job['candidate']
    root=Path(candidate['root'])
    for relative,expected in candidate['files'].items():
        if hashlib.sha256((root/'src'/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError('music_candidate_changed')
    sys.path.insert(0,str(root/'src'))
    from omni_story.library.reference_music import register_reference,match_reference_music,music_query_clip
    from omni_story.library.state import json_sha,write_json
    from omni_story.library.media import sha256_file
    output=Path(job['output']);gallery=[]
    for entry in job['gallery']:
        if json_sha(entry['understanding'])!=entry['understanding_sha256']:
            raise ValueError('music_original_understanding_changed')
        index=register_reference(entry['asset'],entry['understanding'],output/'gallery'/entry['case_id'],
            production_version=entry['production_version'])
        gallery.append(index)
    query=music_query_clip(job['query'],job['window']['start_s'],job['window']['end_s'],output/'query',
        transform=job['window'].get('transform','original'))
    answer=match_reference_music(gallery,query,output,job['receipts'])
    originals={index['entry_id']:entry for index,entry in zip(gallery,job['gallery'])}
    for returned in answer['candidates']:
        entry=originals[returned['entry_id']]
        returned.update(case_id=entry['case_id'],subject_path=entry['subject_path'])
    answer.update(run_sha256=sha256_file(args.run),query=query,gallery_case_ids=[e['case_id'] for e in job['gallery']])
    write_json(output/'answer.json',answer)


if __name__=='__main__':main()
