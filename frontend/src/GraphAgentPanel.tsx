import {BrainCircuit, GitBranch, Play, Terminal} from 'lucide-react';

// Trace panel for POST /api/agents/graph-investigate: shows which tool the agent chose at each
// step, why, what the tool returned, and what the agent concluded. The `catalog` is the allowlist.
const OK=['completed'];
const pillFor=(s:string)=>OK.includes(s)?'verified':(['rejected','rejected_arguments','failed','duplicate_blocked'].includes(s)?'high':'medium');
const TOOL_KIND:any={shortest_path_to_critical_service:'graph algorithm · BFS',rank_entities_pagerank:'graph algorithm · PageRank'};

function ToolResult({tool,result}:{tool:string,result:any}){
 if(!result) return <p className="muted-text">No result was recorded for this step.</p>;
 if(tool==='shortest_path_to_critical_service'&&Array.isArray(result.paths)) return <div className="ga-result">
  <p className="muted-text">{result.algorithm} · {result.graph}</p>
  {result.paths.length===0&&<p className="muted-text">No alerting devices were found for this incident.</p>}
  {result.paths.map((p:any)=><div key={p.device} className="ga-path">
   <div className="ga-path-head"><b>{p.device_name}</b>{p.reachable?<span className="pill verified">{p.hops} hop{p.hops===1?'':'s'} to {p.target_service} ({p.target_criticality})</span>:<span className="pill medium">no recorded route</span>}</div>
   {p.reachable&&<div className="path-chips">{p.path.map((s:any,i:number)=><span key={i} style={{display:'contents'}}>{i>0&&<i className="path-edge">{s.direction==='in'?'◂ ':''}{s.via}{s.direction==='out'?' ▸':''}</i>}<span title={s.label+' · '+s.id}>{s.name}<small className="path-node-type">{s.label}</small></span></span>)}</div>}
  </div>)}
  <p className="muted-text">{result.interpretation}</p></div>;
 if(tool==='rank_entities_pagerank'&&Array.isArray(result.top)){
  const row=(r:any)=><div key={r.id} className="ga-rank"><span className="ga-rank-n">#{r.rank}</span><span><b>{r.name}</b><small>{r.label} · {r.connections} connection{r.connections===1?'':'s'}</small></span><span className="ga-score"><i style={{width:Math.min(100,r.score*400)+'%'}}/><em>{r.score}</em></span></div>;
  return <div className="ga-result"><p className="muted-text">{result.algorithm} · {result.entities_ranked} entities ranked · {result.graph}</p>
   <h5>Most central overall</h5>{result.top.map(row)}
   <h5>This incident's devices</h5>{(result.incident_entities||[]).length?result.incident_entities.map(row):<p className="muted-text">None of this incident's devices appear in the access graph.</p>}
   <p className="muted-text">{result.interpretation}</p></div>;
 }
 return <pre className="ga-raw">{JSON.stringify(result,null,2)}</pre>;
}

export default function GraphAgentPanel({run,busy,live,incident,data}:{run:()=>void,busy:boolean,live:boolean,incident:string,data:any}){
 const trace:any[]=data?.tool_trace||[];
 const obs:any[]=data?.observations||[];
 const used=new Set(trace.filter(t=>t.status==='completed').map(t=>t.tool));
 return <section className="panel" style={{gridColumn:'1 / -1'}}>
  <div className="panel-title"><div><h2>Tool-choosing agent</h2><p>A model picks the next graph tool at every step. Python runs fixed, parameterised Cypher: the model never writes a query. Incident: {incident||'none selected'}</p></div>
   <button className="btn-primary" onClick={run} disabled={busy||!live||!incident}>{busy?'Agent working…':<><Play size={14} fill="currentColor"/> Run tool-choosing agent</>}</button></div>
  {busy&&<p className="muted-text">Waiting for the agent. With a local model each step is a separate model call, so this can take a minute or more.</p>}
  {!data&&!busy&&<p className="muted-text">Not run yet. The trace of every tool call, with its result and the final conclusion, will appear here.</p>}
  {data&&<>
   <div className="reason-section" style={{paddingTop:0}}>
    <span className={'pill '+(data.llm_used?'verified':'medium')}>{data.llm_used?'Model-directed · '+data.model:'Ollama unreachable · tools ran in fixed order'}</span>
    <p className="muted-text" style={{marginTop:9}}>Allowlisted tools (the agent can use nothing else):</p>
    <div className="ga-catalog">{(data.tool_catalog||[]).map((t:string)=><span key={t} className={'ga-tool '+(used.has(t)?'used':'')}>{used.has(t)?'✓ ':''}{t}{TOOL_KIND[t]&&<small>{TOOL_KIND[t]}</small>}</span>)}</div>
   </div>
   <div className="reason-section"><h4><Terminal size={15}/> Tool loop trace ({trace.length} step{trace.length===1?'':'s'})</h4>
    {trace.map((t:any,i:number)=>{const o=obs.find((x:any)=>x.tool===t.tool&&x.result);return <div className="ga-step" key={i}>
     <span className="ga-step-n">{t.step??i+1}</span>
     <div className="ga-step-body">
      <div className="ga-step-head"><b>{t.tool==='finish'?'finish':t.tool}</b>{TOOL_KIND[t.tool]&&<span className="pill neutral">{TOOL_KIND[t.tool]}</span>}</div>
      <p className="muted-text">{t.reason||'No reason recorded.'}</p>
      {o&&<details><summary className="muted-text" style={{cursor:'pointer'}}>Tool result</summary><ToolResult tool={t.tool} result={o.result}/></details>}
      {!o&&t.result_summary&&<small className="muted-text">{t.result_summary}</small>}
     </div>
     <span className={'pill '+pillFor(t.status)}>{String(t.status).replaceAll('_',' ')}</span></div>})}
   </div>
   <div className="reason-callout"><BrainCircuit size={17}/><div><b>Conclusion</b><p>{data.conclusion}</p><p style={{marginTop:9}}><GitBranch size={12} style={{verticalAlign:'-2px'}}/> {data.safety_note}</p></div></div>
  </>}
 </section>;
}
