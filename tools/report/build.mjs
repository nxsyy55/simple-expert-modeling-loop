import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const {marked}=await import(process.env.REPORT_MARKED_PATH ? pathToFileURL(process.env.REPORT_MARKED_PATH).href : 'marked');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const dir=path.join(root,'docs/report');
const base='https://nxsyy55.github.io/simple-expert-modeling-loop/';
const md=fs.readFileSync(path.join(dir,'report.md'),'utf8');
const desc='An evidence-backed technical report on a personal AI research loop: architecture changes, repeated work, 306 historical records, measured gains and resource limits.';
let body=marked.parse(md);const toc=[];let n=0;
body=body.replace(/<h2>(.*?)<\/h2>/g,(_,title)=>{const id=`section-${++n}`;toc.push(`<li><a href="#${id}">${title}</a></li>`);return `<h2 id="${id}">${title}</h2>`;});
body=body.replace(/<table>/g,'<div class="table-scroll"><table>').replace(/<\/table>/g,'</table></div>');
const head=`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A personal test of autonomous machine learning</title><meta name="description" content="${desc}"><meta name="author" content="nxsyy55"><link rel="canonical" href="${base}report/"><meta property="og:type" content="article"><meta property="og:title" content="A personal test of autonomous machine learning"><meta property="og:description" content="${desc}"><meta property="og:url" content="${base}report/"><meta property="og:image" content="${base}report/figures/03-progress.png"><meta name="twitter:card" content="summary_large_image"><link rel="stylesheet" href="style.css"></head>`;
const html=head+`<body><nav><p><a href="../">Project</a> · <a href="https://github.com/nxsyy55/simple-expert-modeling-loop">Source repository</a> · <a href="report.md">Markdown</a> · <a href="data/README.md">Numerical appendix</a></p><details><summary>Contents</summary><ol>${toc.join('')}</ol></details></nav><main>${body}</main><p class="footer">Public working draft v0.1 · 29 October 2026 · Not peer reviewed · Chinese edition planned</p></body></html>`;
fs.writeFileSync(path.join(dir,'index.html'),html);
// Validate every packaged Markdown and HTML local link, including evidence subpages.
const failures=[];let checked=0;
function walk(d){return fs.readdirSync(d,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(path.join(d,e.name)):[path.join(d,e.name)]);}
for(const f of walk(dir).filter(f=>/\.(md|html)$/.test(f))){const s=fs.readFileSync(f,'utf8'); const links=f.endsWith('.md')?[...s.matchAll(/\]\(([^)]+)\)/g)].map(x=>x[1]):[...s.matchAll(/(?:href|src)="([^"]+)"/g)].map(x=>x[1]);for(const target of links){if(/^(https?:|mailto:|data:|#)/.test(target))continue;const p=target.split('#')[0];checked++;if(!fs.existsSync(path.resolve(path.dirname(f),p)))failures.push({file:path.relative(root,f),target});}if(/[A-Z]:[\\/]|file:\/\/[A-Z]:/.test(s))failures.push({file:path.relative(root,f),error:'absolute local path'});}
if(failures.length)throw new Error(JSON.stringify(failures,null,2));
console.log(JSON.stringify({local_links_checked:checked,figures:(html.match(/<img /g)||[]).length,tables:(html.match(/<table>/g)||[]).length,errors:0}));
