import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const packageRoot = process.env.QUANT_DOC_NODE_MODULES || '/Users/jingminhao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const {marked} = await import(pathToFileURL(path.join(packageRoot,'marked/lib/marked.esm.js')));
const root=path.resolve(path.dirname(new URL(import.meta.url).pathname),'..');
const specs=[['design','首版设计','../DESIGN.md'],['daily','每日操作','DAILY_RUNBOOK.md'],['plan','详细规格','IMPLEMENTATION_PLAN.md'],['modules','模块与备选','MODULES_AND_ALTERNATIVES.md'],['tasks','Agent 任务包','AGENT_TASKS.md'],['research','调研依据','RESEARCH_DECISIONS.md']];
const paneByPath=Object.fromEntries(specs.map(([key,label,file])=>[path.resolve(root,'docs',file),key]));
const escape=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
let navigation='',panels='';
for(const [key,label,file] of specs){
 const docPath=path.resolve(root,'docs',file);
 const md=fs.readFileSync(docPath,'utf8');
 const blocks=md.split(/(?=^## )/m); let sections='',links='',n=0;
 for(const block of blocks){
   const title=block.match(/^## (.+)/m)?.[1];
   const id=key+'-'+n++;
   let html=marked.parse(block,{gfm:true});
   html=html.replace(/href="([^\"]+\.md)"/g,(match,file)=>{const key=paneByPath[path.resolve(path.dirname(docPath),file)];return key?'href="#'+key+'" data-panel-link="'+key+'"':match;});
   html=html.replace(/<table>/g,'<div class="table-wrap"><table>').replace(/<\/table>/g,'</table></div>');
   html=html.replace(/<a href="https:/g,'<a target="_blank" rel="noopener noreferrer" href="https:');
   if(key==='plan' && title?.startsWith('3 ')) {
     const diagram='<div class="architecture" role="img" aria-label="数据经过快照、策略、组合风控，进入回测、模拟或人工执行，最终汇入账本和对账报告"><div class="arch-step"><small>可替换数据源</small><b>Tushare / RQData / CSV</b></div><div class="arch-arrow">↓</div><div class="arch-step"><small>统一数据层</small><b>原始数据 → 质量门禁 → 冻结快照</b></div><div class="arch-arrow">↓</div><div class="arch-step"><small>策略和账户决策</small><b>策略 → 目标组合 → 资金分配 → 风控</b></div><div class="arch-arrow">↓</div><div class="arch-branches"><div><b>历史回测</b><small>RQAlphaAdapter</small></div><div><b>持续模拟</b><small>PaperAdapter</small></div><div><b>人工执行</b><small>ManualCsvAdapter</small></div></div><div class="arch-arrow">↓</div><div class="arch-step"><small>共同事实和核对</small><b>成交事件 → 账本 → 对账 → 报告与备份</b></div><p>Harness 通过标准实验规格和候选策略接入评估流程。</p></div>';
     html=html.replace(/<pre>[\s\S]*?<\/pre>/,diagram);
   }
   if(!title) html=html.replace(/<h1>.*?<\/h1>/,'');
   sections+='<section id="'+id+'" class="'+(title?'doc-section':'intro')+'">'+html+'</section>';
   if(title)links+='<a href="#'+id+'">'+escape(title)+'</a>';
 }
 panels+='<article class="panel" data-panel="'+key+'" '+(key!=='design'?'hidden':'')+'>'+sections+'</article>';
 navigation+='<nav data-nav="'+key+'" aria-label="'+label+'章节" '+(key!=='design'?'hidden':'')+'>'+links+'</nav>';
}
const sourceData=JSON.parse(fs.readFileSync(path.join(root,'docs/research/sources.json'),'utf8'));
const sources=sourceData.sources.map(s=>'<li><b>'+s.id+'</b> <a href="'+escape(s.url)+'" target="_blank" rel="noopener noreferrer">'+escape(s.title)+'</a><span>'+escape(s.boundary)+'</span></li>').join('');
panels=panels.replace('</article>','</article>');
const css=`
:root{--bg:#f5f5ef;--ink:#1c302e;--muted:#64716c;--green:#146450;--line:#dce3da;--card:#fff;--soft:#e8f0e7;--gold:#976f28}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:26px}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.9 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
[hidden]{display:none!important}a{color:var(--green);text-underline-offset:3px;overflow-wrap:anywhere}a:hover{color:#123a2d}
button,input{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #ba924a;outline-offset:3px}
.layout{max-width:1560px;margin:auto;padding:32px 38px 70px;display:grid;grid-template-columns:248px minmax(0,1fr);gap:52px}
aside{position:sticky;top:28px;height:calc(100vh - 56px);overflow:auto;padding-right:8px}
.brand{font:600 13px/1.5 ui-monospace,monospace;color:var(--green);letter-spacing:1px;margin:8px 0 22px}.brand small{display:block;font:12px/1.7 sans-serif;color:var(--muted);letter-spacing:0;margin-top:6px}
.tabs{display:grid;gap:6px;margin-bottom:18px}.tabs button{border:1px solid var(--line);background:#fff;border-radius:7px;padding:9px 12px;text-align:left;color:var(--muted);font-size:13px}
.tabs button[aria-selected=true]{background:var(--green);color:white;border-color:var(--green)}
.search{width:100%;border:1px solid var(--line);border-radius:7px;padding:8px 10px;background:white;margin-bottom:14px;font-size:13px;color:var(--ink)}
nav{display:grid;gap:4px}nav a{text-decoration:none;font-size:12px;line-height:1.6;padding:7px 10px;color:var(--muted);border-left:2px solid transparent}
nav a:hover,nav a.active{background:var(--soft);border-left-color:var(--green);color:var(--green)}
.side-footer{font-size:11px;color:var(--muted);border-top:1px solid var(--line);margin-top:22px;padding-top:16px}.plain{border:1px solid var(--line);background:#fff;color:var(--green);border-radius:6px;padding:6px 13px;margin-top:12px}
main{min-width:0;max-width:1120px}.hero{padding:22px 0 25px;border-bottom:1px solid var(--line);margin-bottom:25px}.eyebrow{font:12px ui-monospace,monospace;letter-spacing:1px;color:var(--green);margin-bottom:20px}
h1{font-size:clamp(30px,3.3vw,47px);line-height:1.35;letter-spacing:-1px;margin:0 0 17px}.subtitle{font-size:17px;color:var(--muted);max-width:850px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}.chip{font-size:12px;border:1px solid var(--line);background:#fff;border-radius:30px;padding:4px 11px}.chip.amber{background:#fcf2de;color:#795d26}
.summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px;margin:25px 0 12px}.summary div{padding:17px 19px;border:1px solid var(--line);border-radius:9px;background:#fff}.summary b{font-size:13px;color:var(--green)}.summary p{font-size:13px;color:var(--muted);margin:7px 0 0}
h2{font-size:26px;line-height:1.45;margin:0 0 22px}h3{font-size:19px;line-height:1.6;margin:28px 0 13px}p{margin:0 0 16px}strong{font-weight:650}
.doc-section{padding:34px 0 25px;border-bottom:1px solid var(--line)}.intro{font-size:14px;color:var(--muted);padding:0 0 20px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px;background:#fff;margin:19px 0 24px}table{width:100%;border-collapse:collapse;font-size:13px;line-height:1.85;min-width:650px}
th,td{padding:13px 15px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}th{background:var(--soft);color:#365247;font-weight:600}tr:last-child td{border-bottom:0}td:first-child{min-width:125px}tbody tr:hover{background:#fafcf8}
pre{max-width:100%;overflow:auto;white-space:pre;background:#183a32;color:#eef4e9;border-radius:9px;padding:20px 22px;font:12.5px/1.85 ui-monospace,SFMono-Regular,Consolas,monospace;margin:18px 0 23px}
code{font:12.5px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;background:#e8ece4;border-radius:3px;padding:2px 4px;overflow-wrap:anywhere}pre code{background:none;padding:0;overflow-wrap:normal}
ul,ol{padding-left:25px;margin:14px 0 22px}li{padding-left:2px;margin:7px 0}blockquote{margin:18px 0;border-left:3px solid var(--green);background:var(--soft);padding:18px 22px;border-radius:0 8px 8px 0}blockquote p:last-child{margin-bottom:0}
.architecture{padding:22px;border:1px solid var(--line);border-radius:9px;background:white;margin:20px 0}.arch-step{padding:13px 16px;background:var(--soft);border:1px solid #d6e2d4;border-radius:7px;text-align:center}.architecture small{display:block;color:var(--muted);font-size:11px;font-weight:400}.architecture b{font-size:14px}.arch-arrow{text-align:center;color:var(--green);line-height:1.6;font-size:18px}.arch-branches{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.arch-branches>div{border:1px solid var(--line);border-radius:7px;text-align:center;padding:12px 7px}.architecture p{font-size:12px;color:var(--muted);margin:16px 0 0;text-align:center}
@media(max-width:700px){.architecture{padding:13px}.architecture b{font-size:12px}.arch-branches{grid-template-columns:1fr;gap:6px}.arch-branches>div{padding:7px}.arch-step{padding:11px 7px}}
footer{padding:25px 0;color:var(--muted);font-size:12px}.sources{font-size:12px}.sources span{display:block;color:var(--muted);font-size:11px}.sources b{font:11px ui-monospace,monospace}details{margin:22px 0;border:1px solid var(--line);padding:14px 18px;border-radius:8px;background:#fff}summary{cursor:pointer;color:var(--green);font-weight:600}
.empty{padding:25px;border:1px dashed var(--line);color:var(--muted)}
@media(max-width:1000px){.layout{grid-template-columns:210px minmax(0,1fr);gap:28px;padding:22px}.summary{grid-template-columns:1fr}.summary div{padding:12px 16px}.summary p{margin-top:3px}}
@media(max-width:700px){.layout{display:block;padding:18px}aside{position:static;height:auto;overflow:visible;padding:0}.brand{margin:0 0 13px}.brand small{display:none}.tabs{grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}.tabs button{text-align:center;padding:8px 3px;font-size:12px}nav{display:none!important}.side-footer{display:none}.search{margin-bottom:5px}.hero{padding-top:22px}h1{font-size:31px}h2{font-size:23px}body{font-size:14px;line-height:1.85}.summary{gap:8px}.doc-section{padding-top:28px}pre{font-size:11px;padding:15px}.table-wrap{margin-left:0;margin-right:0}}
@media print{body{background:white;font-size:11px}aside,.hero .summary,.search,.plain{display:none!important}.layout{display:block;padding:0;max-width:none}main{max-width:none}.panel[hidden],section[hidden],details{display:block!important}h1{font-size:28px}h2{font-size:21px}.panel{break-before:page}.panel:first-of-type{break-before:auto}.table-wrap{overflow:visible}table{min-width:0;font-size:10px}th,td{padding:6px 8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef1ec;color:#183a32;font-size:9px}h2,h3{break-after:avoid}.doc-section{padding-top:24px}tr{break-inside:avoid}a{color:inherit}.hero{margin-bottom:18px}}
`;
const script=`
(()=>{let active='design';const input=document.querySelector('.search');const buttons=[...document.querySelectorAll('[data-tab]')];
function search(){const q=input.value.trim().toLowerCase();let count=0;document.querySelectorAll('[data-panel="'+active+'"] section').forEach(s=>{const visible=!q||s.textContent.toLowerCase().includes(q);s.hidden=!visible;if(visible)count++;const a=document.querySelector('[data-nav="'+active+'"] a[href="#'+s.id+'"]');if(a)a.hidden=!visible;});document.getElementById('empty').hidden=!!count;}
function select(name,scroll=true){if(!['design','daily','plan','modules','research','tasks'].includes(name))return;active=name;document.querySelectorAll('.panel').forEach(p=>p.hidden=p.dataset.panel!==name);document.querySelectorAll('[data-nav]').forEach(n=>n.hidden=n.dataset.nav!==name);buttons.forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===name)));input.value='';search();if(scroll)window.scrollTo({top:0,behavior:'smooth'});}
buttons.forEach(b=>b.addEventListener('click',()=>{history.replaceState(null,'','#'+b.dataset.tab);select(b.dataset.tab);}));document.querySelectorAll('[data-panel-link]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();history.replaceState(null,'','#'+a.dataset.panelLink);select(a.dataset.panelLink);}));input.addEventListener('input',search);
document.getElementById('print').addEventListener('click',()=>{document.querySelectorAll('details').forEach(d=>d.open=true);window.print();});
document.querySelectorAll('nav a').forEach(a=>a.addEventListener('click',()=>{document.querySelectorAll('nav a.active').forEach(x=>x.classList.remove('active'));a.classList.add('active');}));
const hash=location.hash.slice(1);if(hash){const section=document.getElementById(hash);select(hash.split('-')[0],false);if(section)setTimeout(()=>section.scrollIntoView(),20);}
})();
`;
const output='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="A 股个人量化最小系统的实施计划、调研依据与 agent 任务包"><title>A 股个人量化系统实现计划</title><style>'+css+'</style></head><body><div class="layout"><aside><div class="brand">A-SHARE / SYSTEM PLAN<small>2026-10-05 · 本地离线文档</small></div><div class="tabs" role="tablist" aria-label="文档类型">'+specs.map(([k,l])=>'<button role="tab" data-tab="'+k+'" aria-selected="'+(k==='design')+'">'+l+'</button>').join('')+'</div><input class="search" type="search" aria-label="搜索当前文档" placeholder="搜索当前文档关键词">'+navigation+'<div class="side-footer">已确认：ETF + 个股<br>执行：人工清单与成交导入<br>Harness：继续比较后选择<br><button class="plain" id="print">打印全部文档</button></div></aside><main><header class="hero"><div class="eyebrow">RESEARCH → IMPLEMENTATION</div><h1>A 股个人量化系统<br>首版设计与每日使用</h1><p class="subtitle">从可复现的数据和策略，到受约束的回测、人工执行、账本和对账。为后续更换数据商、研究工具与策略保留清晰接口。</p><div class="chips"><span class="chip">ETF 与个股</span><span class="chip">日线研究 · 月度调仓示例</span><span class="chip">本地模块化单体</span><span class="chip amber">实施状态：尚未开始</span></div><div class="summary"><div><b>首版闭环</b><p>数据 → 策略 → 回测 → 风控 → 清单 → 成交 → 对账 → 报告。</p></div><div><b>直接交接</b><p>T00–T10 任务、39 个验收案例、逐项完成证据和明确的未决条件。</p></div><div><b>选型依据</b><p>官方文档与固定版本源码；研究结论、设计判断和未实测项分别标注。</p></div></div></header><p id="empty" class="empty" hidden>当前文档没有匹配章节，可切换文档或更换关键词。</p>'+panels+'<details><summary>来源索引 · '+sourceData.source_count+' 项</summary><ol class="sources">'+sources+'</ol></details><footer>本文件是调研与实现规格。页面中的未来 CLI 命令尚未实现；没有已经验证的策略收益。<br>Markdown 源文件与执行任务包位于同目录 docs/，原调研保留在 /Users/jingminhao/quant-projects。</footer></main></div><script>'+script+'</script></body></html>';
fs.writeFileSync(path.join(root,'a-share-minimal-system-plan.html'),output);
console.log(JSON.stringify({output:path.join(root,'a-share-minimal-system-plan.html'),bytes:Buffer.byteLength(output),panels:specs.length,sources:sourceData.source_count}));
