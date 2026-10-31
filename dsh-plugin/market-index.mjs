import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export function createMarketIndex({ commit, publishedAt, version = '0.8.0' }) {
  if (!/^[a-f0-9]{40}$/.test(commit) || /^0{40}$/.test(commit)) throw new Error('Use an existing lowercase 40-character release commit.');
  if (!Number.isSafeInteger(publishedAt) || publishedAt <= 0) throw new Error('publishedAt must be Unix seconds.');
  if (!/^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/.test(version)) throw new Error('Use an exact SemVer version.');
  return {
    format: 1,
    title: { en: 'API-MRI', zh: 'API-MRI' },
    description: { en: 'API-MRI companion for DSH Better Sidebar.', zh: 'API-MRI 的 DSH Better Sidebar 插件。' },
    metadata: { categories: { tools: { title: { en: 'Tools', zh: '工具' } } }, labels: {}, sortKeys: {} },
    plugins: [{
      name: 'dsh-api-mri', title: { en: 'API-MRI', zh: 'API-MRI' },
      description: { en: 'Connect API-MRI to your DSH sidebar.', zh: '在侧栏连接 API-MRI，检查 API 服务与模型指纹。' },
      author: 'API-MRI contributors', homepage: 'https://github.com/Lee-Eee-Eee/API-MRI',
      repository: { url: 'https://github.com/Lee-Eee-Eee/API-MRI' },
      metadata: { category: 'tools', keywords: ['api', 'evaluation', 'fingerprint'], labels: [], sortKeys: {} },
      versions: [{ version, publishTimestamp: publishedAt,
        source: { type: 'git', url: 'https://github.com/Lee-Eee-Eee/API-MRI', commit },
        engines: { dsh: '>=0.2.1-rc.1 <0.3.0' } }]
    }]
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  function argument(name) { const index = args.indexOf(name); return index >= 0 ? args[index + 1] : undefined; }
  const out = argument('--out');
  if (!out) throw new Error('Usage: node dsh-plugin/market-index.mjs --commit <sha> --published-at <unix-seconds> --out <file>');
  const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  const index = createMarketIndex({ commit: argument('--commit'), publishedAt: Number(argument('--published-at')), version: manifest.version });
  await mkdir(path.dirname(path.resolve(out)), { recursive: true });
  await writeFile(out, `${JSON.stringify(index, null, 2)}\n`);
  console.log(`Wrote format-1 market index: ${out}`);
}
