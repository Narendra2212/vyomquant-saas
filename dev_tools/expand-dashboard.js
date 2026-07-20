const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

// 1. Locate the Dashboard function
const dashStartIndex = code.indexOf('function Dashboard({ go }) {');
if (dashStartIndex === -1) {
    console.log("ERROR: Could not find Dashboard function.");
    process.exit(1);
}

// 2. Locate the return statement of the Dashboard function
const returnStartIndex = code.indexOf('return (', dashStartIndex);

// 3. Locate the end of the Dashboard function (marked by the STRATEGIES header)
const dashEndIndex = code.indexOf('// ==============================\n//  PAGE: STRATEGIES', returnStartIndex);

if (returnStartIndex !== -1 && dashEndIndex !== -1) {
    const beforeDashboardReturn = code.substring(0, returnStartIndex);
    const afterDashboard = code.substring(dashEndIndex);

    const expandedDashboardReturn = `return (
    <div style={{padding:"24px 32px",overflowY:"auto",flex:1,display:"flex",flexDirection:"column",background:C.bg0}}>
      {/* Top Stats */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:16,marginBottom:24,flexShrink:0}}>
        {/* Total Portfolio */}
        <Card cls="p-5 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{position:"absolute",top:0,right:0,width:80,height:80,background:"radial-gradient(circle, rgba(0,212,255,0.08) 0%, transparent 70%)"}}/>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
            <span style={{color:C.t2,fontSize:10,fontFamily:"monospace",letterSpacing:2,textTransform:"uppercase"}}>Total Portfolio</span>
            <DollarSign size={14} style={{color:C.cyan}}/>
          </div>
          <div style={{color:C.t1,fontSize:24,fontWeight:900}}>
            {isLoadingStats ? "Loading..." : (topStats?.[0]?.value || "$0.00")}
          </div>
          {!isLoadingStats && topStats?.[0]?.delta && (
            <div style={{display:"flex",alignItems:"center",gap:4,fontSize:11,fontFamily:"monospace",fontWeight:700,color:C.green,marginTop:6}}>
              <ArrowUpRight size={12}/> +{topStats[0].delta}%
            </div>
          )}
        </Card>

        {/* 24H P&L */}
        <Card cls="p-5 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{position:"absolute",top:0,right:0,width:80,height:80,background:"radial-gradient(circle, rgba(34,197,94,0.08) 0%, transparent 70%)"}}/>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
            <span style={{color:C.t2,fontSize:10,fontFamily:"monospace",letterSpacing:2,textTransform:"uppercase"}}>24H P&L</span>
            <TrendingUp size={14} style={{color:C.green}}/>
          </div>
          <div style={{color:C.t1,fontSize:24,fontWeight:900}}>
            {isLoadingStats ? "Loading..." : (topStats?.[1]?.value || "$0.00")}
          </div>
          {!isLoadingStats && topStats?.[1]?.delta && (
            <div style={{display:"flex",alignItems:"center",gap:4,fontSize:11,fontFamily:"monospace",fontWeight:700,color:C.green,marginTop:6}}>
              <ArrowUpRight size={12}/> +{topStats[1].delta}%
            </div>
          )}
        </Card>

        {/* Active Bots */}
        <Card cls="p-5 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{position:"absolute",top:0,right:0,width:80,height:80,background:"radial-gradient(circle, rgba(168,85,247,0.08) 0%, transparent 70%)"}}/>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
            <span style={{color:C.t2,fontSize:10,fontFamily:"monospace",letterSpacing:2,textTransform:"uppercase"}}>Active Bots</span>
            <Bot size={14} style={{color:C.purple}}/>
          </div>
          <div style={{color:C.t1,fontSize:24,fontWeight:900}}>
            {isLoadingStats ? "Loading..." : (topStats?.[2]?.value || "0 / 0")}
          </div>
        </Card>

        {/* Capital Used */}
        <Card cls="p-5 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{position:"absolute",top:0,right:0,width:80,height:80,background:"radial-gradient(circle, rgba(249,115,22,0.08) 0%, transparent 70%)"}}/>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
            <span style={{color:C.t2,fontSize:10,fontFamily:"monospace",letterSpacing:2,textTransform:"uppercase"}}>Capital Used</span>
            <Gauge size={14} style={{color:C.orange}}/>
          </div>
          <div style={{color:C.t1,fontSize:24,fontWeight:900}}>
            {isLoadingStats ? "Loading..." : (topStats?.[3]?.value || "0%")}
          </div>
        </Card>
      </div>

      {/* MIDDLE TIER: Flex:1 enables it to stretch and fill all empty space */}
      <div style={{display:"grid",gridTemplateColumns:"2.5fr 1fr",gap:24,marginBottom:24,flex:1,minHeight:360}}>
        
        {/* Performance & Equity Curve Chart */}
        <Card cls="p-6 flex flex-col">
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:16}}>
            <div style={{display:"flex",alignItems:"center",gap:12}}>
              <span style={{color:C.t1,fontWeight:900,fontSize:16}}>Performance & Equity Curve</span>
              <Tag2 c="cyan">Portfolio Value</Tag2>
              <span style={{color:C.t3,fontSize:10,fontFamily:"monospace"}}>HISTORICAL</span>
            </div>
            <div style={{display:"flex",gap:4}}>
              {["1D","1W","1M","ALL"].map(tf=>(
                <button key={tf} style={{background:tf==="1M"?"rgba(0,212,255,0.12)":"transparent",color:tf==="1M"?C.cyan:C.t3,border:\`1px solid \${tf==="1M"?C.cyan+"40":C.border}\`,borderRadius:6,padding:"4px 12px",fontSize:10,fontFamily:"monospace",cursor:"pointer",fontWeight:tf==="1M"?900:400}} className="hover:text-cyan-400 hover:border-cyan-500/50">{tf}</button>
              ))}
            </div>
          </div>
          <div style={{display:"flex",alignItems:"baseline",gap:10,marginBottom:20}}>
            <span style={{color:C.cyan,fontSize:32,fontWeight:900}}>
              {isLoadingStats ? "$0.00" : (topStats?.[0]?.value || "$0.00")}
            </span>
            {!isLoadingStats && topStats?.[1] && (
              <span style={{color:C.green,fontSize:13,fontFamily:"monospace",fontWeight:900}}>
                \u25b2 {topStats[1]?.value} ({topStats[1]?.delta || "0"}%)
              </span>
            )}
          </div>
          {/* The wrapper below forces Recharts to inherit the dynamic height */}
          <div style={{flex:1, minHeight: 0, position: "relative"}}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityCurve.length > 0 ? equityCurve : EQUITY}>
                <defs>
                  <linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor={C.cyan} stopOpacity={0.25}/>
                    <stop offset="95%" stopColor={C.cyan} stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false}/>
                <XAxis dataKey="d" hide/>
                <YAxis domain={["auto","auto"]} hide/>
                <Tooltip content={<CustomTooltip/>}/>
                <Area dataKey="v" stroke={C.cyan} strokeWidth={2.5} fill="url(#cg)" dot={false}/>
              </AreaChart>
            </ResponsiveContainer>
            {isLoadingEquity&&<div style={{position:"absolute",inset:0,display:"flex",alignItems:"center",justifyContent:"center",color:C.t3,fontSize:12,fontFamily:"monospace",background:"rgba(1,6,8,0.5)"}}>Loading equity curve...</div>}
          </div>
        </Card>

        {/* Active Strategies panel */}
        <Card cls="p-6 flex flex-col">
          <PanelTitle title="Active Strategies" right={<Btn v="ghost" sz="xs" onClick={()=>go("strategies")}>View All</Btn>}/>
          <div style={{display:"flex",flexDirection:"column",gap:12,overflowY:"auto",flex:1,paddingRight:4}} className="custom-scrollbar">
            {isLoadingActiveBots&&<div style={{color:C.t3,fontSize:11,fontFamily:"monospace",padding:"10px 4px"}}>Loading active strategies...</div>}
            {!isLoadingActiveBots&&activeBots.map(s=>(
              <div key={s.id} style={{background:C.bg3,borderRadius:12,padding:16,border:\`1px solid \${C.border}\`}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10}}>
                  <span style={{color:C.t1,fontSize:13,fontWeight:900}}>{s.name}</span>
                  <span style={{color:s.pnl>=0?C.green:C.red,fontSize:12,fontFamily:"monospace",fontWeight:900}}>{s.pnl>=0?"+":""}{s.pnl}%</span>
                </div>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                  <Tag2 c="cyan">{s.pair}</Tag2>
                  <StatusDot status={s.status}/>
                </div>
                <div style={{marginTop:12}}>
                  <ProgressBar v={s.wr} max={100} color={s.pnl>=0?C.green:C.red} h={5}/>
                </div>
              </div>
            ))}
            {!isLoadingActiveBots&&activeBots.length===0&&<div style={{color:C.t3,fontSize:11,fontFamily:"monospace",padding:"10px 4px"}}>No running strategies.</div>}
          </div>
        </Card>
      </div>

      {/* Recent Transactions */}
      <div style={{display:"grid",gridTemplateColumns:"1fr",gap:16,flexShrink:0}}>
        <Card cls="p-6">
          <PanelTitle title="Recent Transactions" right={<Btn v="ghost" sz="xs" onClick={()=>go("history")}>Full Ledger \u2192</Btn>}/>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:"monospace"}}>
            <thead>
              <tr style={{color:C.t3,letterSpacing:3,fontSize:10}}>
                {["TIME","PAIR","SIDE","PRICE","AMOUNT"].map(h=>(
                  <th key={h} style={{textAlign:"left",padding:"10px 14px",fontWeight:900}}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoadingRecent&&(
                <tr>
                  <td colSpan={5} style={{padding:"20px 14px",color:C.t3,fontFamily:"monospace"}}>Loading recent transactions...</td>
                </tr>
              )}
              {!isLoadingRecent&&recentTransactions.length===0&&(
                <tr>
                  <td colSpan={5} style={{padding:"20px 14px",color:C.t3,fontFamily:"monospace",textAlign:"center"}}>No recent transactions found.</td>
                </tr>
              )}
              {recentTransactions.map((r,i)=>(
                <tr key={i} style={{borderTop:\`1px solid \${C.border}44\`}} className="hover:bg-white/5 transition-colors">
                  <td style={{padding:"12px 14px",color:C.t3}}>{r.t}</td>
                  <td style={{padding:"12px 14px",color:C.t1,fontWeight:900}}>{r.p}</td>
                  <td style={{padding:"12px 14px"}}><Tag2 c={r.s==="BUY"?"green":"red"}>{r.s}</Tag2></td>
                  <td style={{padding:"12px 14px",color:C.t1}}>{r.pr}</td>
                  <td style={{padding:"12px 14px",color:C.t2}}>{r.a}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

`;

    code = beforeDashboardReturn + expandedDashboardReturn + afterDashboard;
    fs.writeFileSync(file, code, 'utf8');
    console.log("SUCCESS: Dashboard layout expanded to cover full screen dynamically!");
} else {
    console.log("ERROR: Could not isolate the Dashboard return block.");
}
