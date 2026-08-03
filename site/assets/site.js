(() => {
  'use strict';
  const root = document.body.dataset.root || './';
  const page = document.body.dataset.page || '';
  const el = id => document.getElementById(id);
  const money = value => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD',minimumFractionDigits:2}).format(value);
  const priceMoney = value => new Intl.NumberFormat('en-US', {
    style:'currency', currency:'USD', minimumFractionDigits:value < 1 ? 4 : 2,
    maximumFractionDigits:value < 1 ? 8 : value < 10 ? 6 : 4
  }).format(value);
  const signedMoney = value => `${value >= 0 ? '+' : '−'}${money(Math.abs(value))}`;
  const signedPct = value => `${value >= 0 ? '+' : '−'}${Math.abs(value * 100).toFixed(2)}%`;
  const performancePct = value => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)}%`;
  const safeDate = value => new Date(value).toLocaleString('en-US', {dateStyle:'medium',timeStyle:'short',timeZone:'UTC'}) + ' UTC';
  const addCell = (row, text, className='') => { const cell=document.createElement('td'); cell.textContent=text; if(className)cell.className=className; row.append(cell); return cell; };

  async function json(path) {
    const response = await fetch(root + path, {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function statusLabel(status) {
    return ({normal:'Qualifying findings',no_findings:'No qualifying findings',risk_off:'Risk-off protection',data_failure:'Data unavailable'})[status] || 'Unknown';
  }

  function renderFindingRows(snapshot) {
    const body=el('finding-rows'); if(!body)return;
    body.replaceChildren();
    if(!snapshot.findings.length){ const row=document.createElement('tr'); addCell(row,statusLabel(snapshot.status)); row.firstChild.colSpan=7; body.append(row); return; }
    snapshot.findings.forEach(item=>{
      const row=document.createElement('tr');
      addCell(row,item.product_id.replace('-USD',''),'asset');
      addCell(row,money(item.reference_price));
      addCell(row,money(item.suggested_limit_buy));
      addCell(row,money(item.do_not_chase_above));
      addCell(row,money(item.possible_target));
      addCell(row,signedPct(item.estimated_net_upside),item.estimated_net_upside>=0?'positive':'negative');
      addCell(row,`${item.reward_to_risk.toFixed(2)}×`);
      body.append(row);
    });
  }

  function renderLatest(snapshot) {
    const badge=el('scan-status'); if(badge){badge.textContent=statusLabel(snapshot.status);badge.className=`status ${snapshot.status}`;}
    if(el('scan-time'))el('scan-time').textContent=`Observed ${safeDate(snapshot.observed_at)}`;
    const stale=Date.now()>Date.parse(snapshot.current_until);
    if(el('scan-freshness')){el('scan-freshness').textContent=stale?'Historical snapshot—the next scan is overdue':`Current until ${safeDate(snapshot.current_until)}`;el('scan-freshness').className=`freshness${stale?' stale':''}`;}
    if(el('finding-count'))el('finding-count').textContent=String(snapshot.finding_count);
    if(el('products-loaded'))el('products-loaded').textContent=`${snapshot.market_regime.products_loaded}/10`;
    if(el('market-state'))el('market-state').textContent=snapshot.market_regime.risk_off?'Risk off':'Normal';
    if(el('breadth'))el('breadth').textContent=snapshot.market_regime.below_sma_fraction==null?'Unavailable':`${(snapshot.market_regime.below_sma_fraction*100).toFixed(0)}% below SMA`;
    if(el('scan-note'))el('scan-note').textContent=snapshot.notice;
    renderFindingRows(snapshot);
  }

  function renderArchive(index) {
    const list=el('snapshot-archive'); if(!list)return; list.replaceChildren();
    index.snapshots.slice(0,48).forEach(entry=>{ const item=document.createElement('li'); const time=document.createElement('span');time.textContent=safeDate(entry.observed_at);const state=document.createElement('span');state.textContent=statusLabel(entry.status);const count=document.createElement('span');count.textContent=`${entry.finding_count} finding${entry.finding_count===1?'':'s'}`;item.append(time,state,count);list.append(item); });
  }

  function renderPerformance(data) {
    const s=data.summary;
    if(el('net-pnl')){el('net-pnl').textContent=signedMoney(s.net_realized_pnl);el('net-pnl').className=s.net_realized_pnl>=0?'positive':'negative';}
    if(el('trade-count'))el('trade-count').textContent=String(s.completed_trades);
    if(el('record'))el('record').textContent=`${s.wins} / ${s.losses}`;
    if(el('realized-return')){el('realized-return').textContent=performancePct(s.realized_return_pct);el('realized-return').className=s.realized_return_pct>=0?'positive':'negative';}
    if(el('performance-updated'))el('performance-updated').textContent=`Sanitized ledger refreshed ${new Date(data.generated_at).toLocaleDateString('en-US',{year:'numeric',month:'long',day:'numeric',timeZone:'UTC'})}`;
    const body=el('trade-rows'); if(!body)return; body.replaceChildren();
    [...data.trades].reverse().forEach(trade=>{ const row=document.createElement('tr');addCell(row,trade.asset,'asset');addCell(row,trade.entry_date);addCell(row,trade.exit_date);addCell(row,priceMoney(trade.buy_price));addCell(row,priceMoney(trade.sell_price));addCell(row,trade.held_days.toFixed(2));addCell(row,money(trade.cost));addCell(row,signedMoney(trade.pnl),trade.pnl>=0?'positive':'negative');addCell(row,performancePct(trade.return_pct),trade.return_pct>=0?'positive':'negative');addCell(row,trade.beat_yield?'Beat':'Missed');body.append(row); });
  }

  async function start() {
    if(el('year'))el('year').textContent=String(new Date().getFullYear());
    try {
      if(page==='home'||page==='findings'){ const [latest,index]=await Promise.all([json('data/findings/latest.json'),json('data/findings/index.json')]);renderLatest(latest);renderArchive(index); }
      if(page==='home'||page==='performance'){ renderPerformance(await json('data/performance/realized-results.json')); }
    } catch(error) {
      if(el('scan-status')){el('scan-status').textContent='Data unavailable';el('scan-status').className='status data_failure';}
      if(el('scan-note'))el('scan-note').textContent='Public data could not be loaded. Do not rely on stale values.';
      if(el('performance-updated'))el('performance-updated').textContent='Performance data unavailable';
      console.error('Public data load failed',error);
    }
  }
  start();
})();
