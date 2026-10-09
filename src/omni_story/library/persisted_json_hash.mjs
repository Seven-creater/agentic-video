import crypto from 'node:crypto';
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const require = (condition, reason) => { if (!condition) throw new Error('library_mcp_opencode_' + reason); };
// Python json_sha retains 0.0 and 1e-05, while JSON.stringify changes them to
// 0 and 0.00001. Preserve the persisted number tokens while sorting object keys.
export function persistedJsonHash(raw) {
  JSON.parse(raw);
  const tokens = raw.match(/"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\],:]/g);
  let at = 0;
  const next = expected => require(tokens[at++] === expected, 'recorded_json_invalid');
  function value() {
    const token = tokens[at++];
    if (token === '{') {
      const entries = new Map();
      while (tokens[at] !== '}') {
        const key = JSON.parse(tokens[at++]);
        require(typeof key === 'string' && !entries.has(key), 'recorded_json_invalid');
        next(':');
        entries.set(key, value());
        if (tokens[at] !== '}') next(',');
      }
      next('}');
      return '{' + [...entries].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
        .map(([key, item]) => JSON.stringify(key) + ':' + item).join(',') + '}';
    }
    if (token === '[') {
      const entries = [];
      while (tokens[at] !== ']') {
        entries.push(value());
        if (tokens[at] !== ']') next(',');
      }
      next(']');
      return '[' + entries.join(',') + ']';
    }
    return token.startsWith('"') ? JSON.stringify(JSON.parse(token)) : token;
  }
  const canonical = value();
  require(at === tokens.length, 'recorded_json_invalid');
  return sha(canonical);
}
