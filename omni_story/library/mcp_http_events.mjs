// Read only this job's HTTP events without converting the entire journal into
// one V8 string. No request, response, or model body is changed.
import fs from 'node:fs';

export function jobHttpEvents(file, jobId) {
  if (!fs.existsSync(file)) return [];
  const fd = fs.openSync(file, 'r'), chunk = Buffer.alloc(256 * 1024);
  const needle = Buffer.from(jobId), records = [];
  let carry = Buffer.alloc(0);
  const inspect = bytes => {
    if (!bytes.includes(needle)) return;
    const value = JSON.parse(bytes.toString('utf8'));
    if (value.job_id === jobId) records.push(value);
  };
  try {
    let size;
    while ((size = fs.readSync(fd, chunk, 0, chunk.length, null))) {
      const buffer = Buffer.concat([carry, chunk.subarray(0, size)]);
      let start = 0, end;
      while ((end = buffer.indexOf(10, start)) !== -1) {
        if (end > start) inspect(buffer.subarray(start, end));
        start = end + 1;
      }
      carry = Buffer.from(buffer.subarray(start));
    }
    if (carry.length) inspect(carry);
  } finally { fs.closeSync(fd); }
  return records;
}
