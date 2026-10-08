"""Offline evidence sheets with real decoder timestamps (requires av, Pillow).

This is observation material, not an automatic shot detector or semantic review.
It samples the first actual decoded frame at/after each requested timestamp.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import av
from PIL import Image, ImageDraw, ImageFont


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float)
    parser.add_argument('--step', type=float, default=.25)
    parser.add_argument('--crop', help='x,y,width,height; crops sheets, preserves full individual frames')
    parser.add_argument('--cell-width', type=int, default=480)
    args = parser.parse_args()
    if (not math.isfinite(args.start) or args.start < 0 or
            not math.isfinite(args.step) or args.step <= 0 or args.cell_width < 160 or
            args.end is not None and (not math.isfinite(args.end) or args.end <= args.start)):
        parser.error('invalid observation interval, step or width')
    crop = tuple(map(int, args.crop.split(','))) if args.crop else None
    if crop and (len(crop) != 4 or min(crop[:2]) < 0 or min(crop[2:]) < 1):
        parser.error('invalid crop')
    source = args.video.resolve()
    digest = sha(source)
    out = args.output.resolve()
    records, panels = [], []
    target_index = 0
    container = av.open(str(source))
    stream = container.streams.video[0]
    if crop and (crop[0]+crop[2] > stream.width or crop[1]+crop[3] > stream.height):
        container.close()
        parser.error('crop outside decoded video')
    out.mkdir(parents=True, exist_ok=False)  # Preserve earlier evidence.
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path), 22) if font_path.exists() else ImageFont.load_default()
    total_decoded, last_pts = 0, None
    sheets=[]

    def flush_sheet():
        if not panels:
            return
        cols=min(3,len(panels)); rows=math.ceil(len(panels)/cols)
        sheet=Image.new('RGB',(cols*panels[0].width, rows*panels[0].height),'#dddddd')
        for i,panel in enumerate(panels):
            sheet.paste(panel,((i%cols)*panel.width,(i//cols)*panel.height))
        name=f'sheet_{len(sheets):03d}.jpg'
        sheet.save(out/name,quality=93)
        sheets.append(dict(image=name,sha256=sha(out/name),
                           first_record=len(records)-len(panels),last_record=len(records)-1))
        panels.clear()  # Bound grid pixel memory independently of video length.

    for n, frame in enumerate(container.decode(stream)):
        total_decoded += 1
        if frame.pts is None:
            raise ValueError('frame has no presentation timestamp')
        timestamp = float(frame.pts * frame.time_base)
        last_pts = timestamp
        target = args.start + target_index * args.step
        if args.end is not None and timestamp >= args.end - 1e-9:
            break
        if timestamp < target - 1e-9:
            continue
        image = frame.to_image().convert('RGB')
        name = f'frame_{n:06d}_{timestamp:.6f}.jpg'
        image.save(out/name, quality=92)
        records.append(dict(decode_frame_index=n, pts=frame.pts,
            time_base=str(frame.time_base), timestamp_s=timestamp,
            requested_target_s=target, image=name, image_sha256=sha(out/name)))
        if crop:
            x, y, w, h = crop
            image = image.crop((x, y, x+w, y+h))
        width = args.cell_width
        height = round(image.height*width/image.width)
        panel = Image.new('RGB', (width, height+34), 'white')
        panel.paste(image.resize((width,height)), (0,34))
        ImageDraw.Draw(panel).text((6,5), f'#{n}  {timestamp:.6f}s', font=font, fill='black')
        panels.append(panel)
        if len(panels) == 9:
            flush_sheet()
        target_index = math.floor((timestamp-args.start)/args.step + 1e-7)+1
    container.close()
    if not records:
        raise ValueError('no frames in requested interval')
    flush_sheet()
    if sha(source) != digest:
        raise ValueError('source changed during decode')
    manifest=dict(source=str(source),source_sha256=digest,request=dict(
        start_s=args.start,end_exclusive_s=args.end,step_s=args.step,crop=crop),
        sampling='first decoded PTS at or after target, never synthetic frames',
        limitations=['Sampled images do not establish continuous playback or music perception.',
                     'A missed instant must be inspected at finer sampling with adjacent context.'],
        decoded_frames=total_decoded,last_decoded_pts_s=last_pts,records=records,sheets=sheets)
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),frames=len(records),sheets=len(sheets)),ensure_ascii=False))


if __name__ == '__main__':
    main()
