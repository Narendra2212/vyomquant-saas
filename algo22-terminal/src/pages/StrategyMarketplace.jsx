import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from "react-router-dom";
import { 
  Search, Filter, TrendingUp, Users, Star, ArrowRight, Zap, 
  Shield, Activity, Cpu, Hexagon, X, Check, AlertTriangle, ArrowLeft
} from 'lucide-react';
import { useAppState } from '../AppState';
import client, { publicGet } from '../apiClient';

const StrategyMarketplace = () => {
  const navigate = useNavigate();
  const { uiMode } = useAppState();
  const [view, setView] = useState('browse'); // 'browse', 'detail', 'my_strategies', 'admin_pending'
  
  // Data states
  const [strategies, setStrategies] = useState([]);
  const [myStrategies, setMyStrategies] = useState([]);
  const [pendingStrategies, setPendingStrategies] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  
  // Pagination & Filter state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [sort, setSort] = useState('clones');
  
  // UI states
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [cloneLoading, setCloneLoading] = useState(false);
  
  // Ratings UI
  const [ratingInput, setRatingInput] = useState(0);
  const [reviewInput, setReviewInput] = useState('');
  const [ratingLoading, setRatingLoading] = useState(false);

  // Fetch Catalogue
  const fetchStrategies = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Build query string
      const params = { page, limit: 12, sort };
      if (searchQuery) params.q = searchQuery;
      
      const token = sessionStorage.getItem('token');
      let data;
      if (token) {
        const res = await client.get('/api/library', { params });
        data = res.data;
      } else {
        data = await publicGet('/api/library', params);
      }
      setStrategies(data.items || []);
      setTotalPages(data.pages || 1);
    } catch (err) {
      console.error(err);
      setError('Failed to load marketplace data. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [page, sort, searchQuery]);

  // Fetch My Strategies
  const fetchMyStrategies = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await client.get('/api/library/me');
      setMyStrategies(res.data.items || []);
    } catch (err) {
      console.error(err);
      setError('Failed to load your published strategies.');
    } finally {
      setLoading(false);
    }
  };

  // Fetch Admin Pending
  const fetchPending = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await client.get('/api/admin/library/pending');
      setPendingStrategies(res.data.items || []);
    } catch (err) {
      console.error(err);
      setError('Failed to load pending strategies. Are you an admin?');
    } finally {
      setLoading(false);
    }
  };

  // Initial load
  useEffect(() => {
    let isMounted = true;
    if (view === 'browse') fetchStrategies();
    else if (view === 'my_strategies') fetchMyStrategies();
    else if (view === 'admin_pending') fetchPending();
    return () => { isMounted = false; };
  }, [view, fetchStrategies]);

  // Fetch Detail
  const loadDetail = async (id) => {
    setLoading(true);
    setError(null);
    try {
      const token = sessionStorage.getItem('token');
      let data;
      if (token) {
        const res = await client.get(`/api/library/${id}`);
        data = res.data;
      } else {
        data = await publicGet(`/api/library/${id}`);
      }
      setSelectedStrategy(data);
      setView('detail');
    } catch (err) {
      console.error(err);
      setError('Failed to load strategy details.');
    } finally {
      setLoading(false);
    }
  };

  // Clone Flow
  const handleClone = async (strat) => {
    setCloneLoading(true);
    try {
      const res = await client.post(`/api/library/${strat.id}/clone`);
      // Notify user, then go to builder
      alert(res.data.message || 'Strategy cloned successfully!');
      
      // In a real flow, we might fetch the newly cloned strategy from /api/strategies
      // For now, redirect to builder.
      navigate("/app/builder");
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to clone strategy.');
    } finally {
      setCloneLoading(false);
    }
  };

  // Submit Rating
  const submitRating = async () => {
    if (!ratingInput) return alert('Please select a star rating.');
    setRatingLoading(true);
    try {
      await client.post(`/api/library/${selectedStrategy.id}/rate`, {
        rating: ratingInput,
        review_text: reviewInput || undefined
      });
      alert('Rating submitted successfully!');
      // Reload detail
      loadDetail(selectedStrategy.id);
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to submit rating. Did you clone it first?');
    } finally {
      setRatingLoading(false);
    }
  };

  // Unpublish
  const handleUnpublish = async (id) => {
    if (!window.confirm('Are you sure you want to unpublish this strategy? Existing clones are unaffected.')) return;
    try {
      await client.delete(`/api/library/${id}`);
      alert('Strategy unpublished.');
      fetchMyStrategies();
    } catch (err) {
      alert(err.message || 'Failed to unpublish.');
    }
  };

  // Admin Actions
  const handleModerate = async (id, status, isFeatured = false) => {
    try {
      await client.patch(`/api/admin/library/${id}`, {
        moderation_status: status,
        is_featured: isFeatured
      });
      alert(`Strategy ${status}.`);
      if (view === 'admin_pending') fetchPending();
      else if (view === 'detail') loadDetail(id);
    } catch (err) {
      alert(err.message || 'Moderation failed.');
    }
  };

  // Renders
  const renderCard = (strat) => (
    <div key={strat.id} className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg flex flex-col gap-4 group hover:border-[#00D4FF]50 transition-all cursor-default relative overflow-hidden">
      {strat.is_featured && (
        <div className="absolute top-0 right-0 bg-[#FFB74D] text-[#080A0D] text-[9px] font-bold px-2 py-0.5 rounded-bl font-mono uppercase">
          Featured
        </div>
      )}
      <div className="flex justify-between items-start">
        <div>
          <h3 className="font-bold text-lg text-[#E6EDF3] leading-tight cursor-pointer hover:text-[#00D4FF] transition-colors" onClick={() => loadDetail(strat.id)}>{strat.name}</h3>
          <div className="text-[#8B949E] text-xs font-mono mt-1">by <span className="text-[#00D4FF] font-bold">{strat.author_alias || 'Unknown'}</span></div>
        </div>
        <span className="bg-[#080A0D] border border-[#202938] px-2 py-1 rounded text-[10px] font-mono text-[#8B949E] uppercase tracking-widest shrink-0">
          {strat.category}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {strat.tags && strat.tags.map(tag => (
          <span key={tag} className="text-[9px] border border-[#202938] bg-[#1A222C] text-[#8B949E] px-2 py-0.5 rounded font-mono uppercase">
            {tag}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-2 py-4 border-y border-[#202938] mt-2">
        <div className="flex flex-col gap-1">
          <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><Users size={12}/> Clones</span>
          <span className="text-[#E6EDF3] font-bold font-mono">{strat.clone_count?.toLocaleString() || 0}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><TrendingUp size={12}/> P&L</span>
          <span className={`font-bold font-mono ${strat.backtest_total_return_pct >= 0 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
            {strat.backtest_total_return_pct > 0 ? '+' : ''}{(strat.backtest_total_return_pct || 0).toFixed(2)}%
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><Activity size={12}/> Sharpe</span>
          <span className="text-[#FFB74D] font-bold font-mono">{(strat.backtest_sharpe_ratio || 0).toFixed(2)}</span>
        </div>
      </div>

      <div className="flex justify-between items-center mt-auto pt-2">
        <span className="text-[10px] font-mono font-bold tracking-widest flex items-center gap-1 text-[#FFB74D]">
          <Star size={12} fill="currentColor" /> {strat.avg_rating ? strat.avg_rating.toFixed(1) : 'New'} 
          <span className="text-[#8B949E] font-normal">({strat.rating_count || 0})</span>
        </span>
        <button 
          onClick={(e) => { e.stopPropagation(); handleClone(strat); }}
          disabled={cloneLoading || strat.user_has_cloned}
          className={`px-4 py-2 rounded text-xs font-bold font-mono flex items-center gap-2 transition-colors ${
            strat.user_has_cloned ? 'bg-[#131722] border border-[#202938] text-[#8B949E] cursor-not-allowed' : 'bg-[#00D4FF] hover:bg-[#00D4FF]90 text-white'
          }`}
        >
          {strat.user_has_cloned ? 'Cloned' : 'Clone'} <ArrowRight size={14} />
        </button>
      </div>
    </div>
  );

  const renderDetail = () => {
    if (!selectedStrategy) return null;
    const strat = selectedStrategy;
    
    return (
      <div className="flex flex-col gap-6">
        <button onClick={() => setView('browse')} className="flex items-center gap-2 text-[#8B949E] hover:text-[#E6EDF3] transition-colors w-max font-mono text-sm">
          <ArrowLeft size={16} /> Back to Library
        </button>
        
        <div className="bg-[#131722] border border-[#202938] rounded-xl p-8 shadow-lg flex flex-col gap-6">
          <div className="flex justify-between items-start">
            <div>
              <h1 className="text-3xl font-black tracking-tight text-[#E6EDF3]">{strat.name}</h1>
              <div className="text-[#8B949E] font-mono mt-2">Created by <span className="text-[#00D4FF] font-bold">{strat.author_alias || 'Unknown'}</span></div>
            </div>
            <div className="flex flex-col items-end gap-2">
              <span className="bg-[#080A0D] border border-[#202938] px-3 py-1.5 rounded text-xs font-mono text-[#8B949E] uppercase tracking-widest">
                {strat.category}
              </span>
              <button 
                onClick={() => handleClone(strat)}
                disabled={cloneLoading || strat.user_has_cloned}
                className={`px-6 py-3 rounded text-sm font-bold font-mono flex items-center gap-2 transition-colors ${
                  strat.user_has_cloned ? 'bg-[#131722] border border-[#202938] text-[#8B949E] cursor-not-allowed' : 'bg-[#26A69A] hover:bg-[#26A69A]90 text-white shadow-[0_0_15px_rgba(38,166,154,0.3)]'
                }`}
              >
                {strat.user_has_cloned ? 'Cloned to Builder' : 'Clone Strategy'} <ArrowRight size={16} />
              </button>
            </div>
          </div>
          
          <div className="text-[#C9D1D9] leading-relaxed max-w-4xl whitespace-pre-wrap">
            {strat.description || 'No description provided.'}
          </div>
          
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 py-6 border-y border-[#202938]">
            <div className="flex flex-col gap-1 bg-[#080A0D] p-4 rounded-lg border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest">Total Return</span>
              <span className={`text-xl font-bold font-mono ${strat.backtest_total_return_pct >= 0 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
                {strat.backtest_total_return_pct > 0 ? '+' : ''}{(strat.backtest_total_return_pct || 0).toFixed(2)}%
              </span>
            </div>
            <div className="flex flex-col gap-1 bg-[#080A0D] p-4 rounded-lg border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest">Sharpe Ratio</span>
              <span className="text-xl font-bold font-mono text-[#FFB74D]">{(strat.backtest_sharpe_ratio || 0).toFixed(2)}</span>
            </div>
            <div className="flex flex-col gap-1 bg-[#080A0D] p-4 rounded-lg border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest">Win Rate</span>
              <span className="text-xl font-bold font-mono text-[#E6EDF3]">{(strat.backtest_win_rate_pct || 0).toFixed(1)}%</span>
            </div>
            <div className="flex flex-col gap-1 bg-[#080A0D] p-4 rounded-lg border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest">Max Drawdown</span>
              <span className="text-xl font-bold font-mono text-[#EF5350]">{(strat.backtest_max_drawdown_pct || 0).toFixed(2)}%</span>
            </div>
          </div>
          
          {/* Equity Curve Placeholder */}
          <div className="bg-[#080A0D] border border-[#202938] rounded-lg p-6 h-64 flex flex-col items-center justify-center text-[#8B949E] font-mono text-sm relative overflow-hidden">
             {strat.equity_curve_snapshot && strat.equity_curve_snapshot.length > 0 ? (
               <div className="absolute inset-0 flex items-center justify-center">
                 <div className="w-full h-full opacity-50 px-4 pt-4">
                   {/* Crude SVG sparkline since recharts might not be imported */}
                   <svg width="100%" height="100%" preserveAspectRatio="none">
                     <polyline 
                       fill="none" 
                       stroke="#00D4FF" 
                       strokeWidth="2" 
                       points={
                         strat.equity_curve_snapshot.map((p, i, arr) => {
                           const min = Math.min(...arr.map(a => a.equity));
                           const max = Math.max(...arr.map(a => a.equity));
                           const range = max - min || 1;
                           const x = (i / (arr.length - 1)) * 100;
                           const y = 100 - (((p.equity - min) / range) * 100);
                           return `${x}%,${y}%`;
                         }).join(' ')
                       }
                     />
                   </svg>
                 </div>
               </div>
             ) : (
               <>
                 <Activity size={32} className="mb-2 opacity-50" />
                 Equity curve rendering requires D3/Recharts component.
               </>
             )}
          </div>
          
          {/* Ratings & Reviews */}
          <div className="flex flex-col gap-4 mt-4">
            <h3 className="font-bold text-lg border-b border-[#202938] pb-2">Community Ratings</h3>
            
            {strat.user_has_cloned && (
              <div className="bg-[#080A0D] border border-[#202938] p-4 rounded-lg flex flex-col gap-3">
                <h4 className="font-mono text-sm text-[#8B949E]">Leave a Verified Review</h4>
                <div className="flex gap-2">
                  {[1,2,3,4,5].map(v => (
                    <button key={v} onClick={() => setRatingInput(v)} className="focus:outline-none">
                      <Star size={24} className={ratingInput >= v || strat.user_rating >= v ? "text-[#FFB74D]" : "text-[#202938]"} fill={(ratingInput >= v || strat.user_rating >= v) ? "currentColor" : "none"} />
                    </button>
                  ))}
                </div>
                <textarea 
                  className="bg-[#131722] border border-[#202938] rounded p-2 text-sm text-[#E6EDF3] outline-none h-20 font-mono"
                  placeholder="Optional review text..."
                  value={reviewInput}
                  onChange={e => setReviewInput(e.target.value)}
                />
                <button 
                  onClick={submitRating}
                  disabled={ratingLoading || !ratingInput}
                  className="bg-[#00D4FF] hover:bg-[#00D4FF]90 disabled:opacity-50 text-white px-4 py-2 rounded text-xs font-bold font-mono w-max"
                >
                  {ratingLoading ? 'Submitting...' : 'Submit Rating'}
                </button>
              </div>
            )}
            
            {!strat.user_has_cloned && sessionStorage.getItem('token') && (
              <div className="text-sm font-mono text-[#8B949E] bg-[#080A0D] p-3 rounded border border-[#202938]">
                You must clone this strategy to leave a rating.
              </div>
            )}
            
            <div className="flex flex-col gap-3 mt-2">
              {strat.recent_ratings && strat.recent_ratings.length > 0 ? (
                strat.recent_ratings.map((r, i) => (
                  <div key={i} className="bg-[#131722] border border-[#202938] p-4 rounded-lg flex flex-col gap-2">
                    <div className="flex justify-between items-center">
                      <div className="flex gap-1 text-[#FFB74D]">
                        {[1,2,3,4,5].map(v => <Star key={v} size={14} fill={v <= r.rating ? "currentColor" : "none"} className={v > r.rating ? "text-[#202938]" : ""} />)}
                      </div>
                      <span className="text-[10px] font-mono text-[#8B949E]">{new Date(r.created_at).toLocaleDateString()}</span>
                    </div>
                    {r.review_text && <p className="text-sm text-[#C9D1D9]">{r.review_text}</p>}
                  </div>
                ))
              ) : (
                <p className="text-sm text-[#8B949E] font-mono">No reviews yet.</p>
              )}
            </div>
            
            {/* Admin Controls */}
            {sessionStorage.getItem('token') && view === 'detail' && (
               <div className="mt-8 border-t border-[#202938] pt-4">
                 <button onClick={() => handleModerate(strat.id, 'featured', true)} className="bg-[#FFB74D] text-black px-4 py-2 rounded text-xs font-bold mr-2">Admin: Feature</button>
                 <button onClick={() => handleModerate(strat.id, 'rejected')} className="bg-[#EF5350] text-white px-4 py-2 rounded text-xs font-bold">Admin: Reject</button>
               </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderMyStrategies = () => (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold font-mono">My Published Strategies</h2>
      </div>
      <div className="bg-[#131722] border border-[#202938] rounded-xl overflow-hidden">
        <table className="w-full text-left font-mono text-sm">
          <thead className="bg-[#080A0D] border-b border-[#202938]">
            <tr>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Name</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Status</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Clones</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Rating</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Actions</th>
            </tr>
          </thead>
          <tbody>
            {myStrategies.length === 0 ? (
              <tr><td colSpan="5" className="p-8 text-center text-[#8B949E]">No published strategies yet. Publish from the Strategy Builder.</td></tr>
            ) : myStrategies.map(s => (
              <tr key={s.id} className="border-b border-[#202938] hover:bg-[#1A222C]">
                <td className="p-4 font-bold text-[#E6EDF3] cursor-pointer" onClick={() => loadDetail(s.id)}>{s.name}</td>
                <td className="p-4">
                  <span className={`px-2 py-1 rounded text-[10px] uppercase tracking-widest ${
                    s.moderation_status === 'approved' || s.moderation_status === 'featured' ? 'bg-[#26A69A]20 text-[#26A69A]' :
                    s.moderation_status === 'pending' ? 'bg-[#FFB74D]20 text-[#FFB74D]' : 'bg-[#EF5350]20 text-[#EF5350]'
                  }`}>
                    {s.is_active ? s.moderation_status : 'Unpublished'}
                  </span>
                </td>
                <td className="p-4">{s.clone_count}</td>
                <td className="p-4 text-[#FFB74D]">{s.avg_rating?.toFixed(1) || 'â€”'}</td>
                <td className="p-4">
                  {s.is_active && (
                    <button onClick={() => handleUnpublish(s.id)} className="text-[#EF5350] text-xs font-bold hover:underline">Unpublish</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );

  const renderAdminPending = () => (
    <div className="flex flex-col gap-6">
      <h2 className="text-2xl font-bold font-mono text-[#FFB74D]">Admin Moderation Queue</h2>
      <div className="bg-[#131722] border border-[#202938] rounded-xl overflow-hidden">
        <table className="w-full text-left font-mono text-sm">
          <thead className="bg-[#080A0D] border-b border-[#202938]">
            <tr>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Strategy</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Author</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Submitted</th>
              <th className="p-4 text-[#8B949E] font-normal uppercase tracking-widest text-[10px]">Actions</th>
            </tr>
          </thead>
          <tbody>
            {pendingStrategies.length === 0 ? (
              <tr><td colSpan="4" className="p-8 text-center text-[#8B949E]">No pending strategies in queue.</td></tr>
            ) : pendingStrategies.map(s => (
              <tr key={s.id} className="border-b border-[#202938] hover:bg-[#1A222C]">
                <td className="p-4 font-bold text-[#E6EDF3] cursor-pointer hover:text-[#00D4FF]" onClick={() => loadDetail(s.id)}>
                  {s.name} <span className="text-xs font-normal text-[#8B949E]">({s.category})</span>
                </td>
                <td className="p-4">{s.author_alias}</td>
                <td className="p-4">{new Date(s.published_at).toLocaleString()}</td>
                <td className="p-4 flex gap-2">
                  <button onClick={() => handleModerate(s.id, 'approved')} className="bg-[#26A69A] text-[#080A0D] px-3 py-1 rounded text-xs font-bold">Approve</button>
                  <button onClick={() => handleModerate(s.id, 'rejected')} className="bg-[#EF5350] text-[#080A0D] px-3 py-1 rounded text-xs font-bold">Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );

  return (
    <div className="flex-1 p-6 overflow-y-auto bg-[#080A0D] text-[#E6EDF3] font-['Inter'] relative">
      <div className="max-w-6xl mx-auto flex flex-col gap-8">
        
        {/* Header & Nav */}
        <div className="flex justify-between items-end">
          <div className="flex flex-col gap-2">
            <h1 className="text-3xl font-black tracking-tight flex items-center gap-3">
              <Hexagon className="text-[#00D4FF]" size={32} /> Strategy Marketplace
            </h1>
            <p className="text-[#8B949E] font-mono text-sm max-w-2xl">
              {uiMode === 'beginner' 
                ? 'Discover and copy proven strategies from top traders. One-click deploy directly to your portfolio.'
                : 'Access institutional-grade algorithms. Fork public logic graphs and deploy to your proprietary engine.'}
            </p>
          </div>
          
          <div className="flex bg-[#131722] border border-[#202938] rounded-lg overflow-hidden font-mono text-xs">
            <button 
              onClick={() => { setView('browse'); fetchStrategies(); }}
              className={`px-4 py-2 transition-colors ${view === 'browse' ? 'bg-[#00D4FF] text-white font-bold' : 'text-[#8B949E] hover:text-[#E6EDF3]'}`}
            >
              Browse
            </button>
            {sessionStorage.getItem('token') && (
              <button 
                onClick={() => { setView('my_strategies'); fetchMyStrategies(); }}
                className={`px-4 py-2 transition-colors border-l border-[#202938] ${view === 'my_strategies' ? 'bg-[#00D4FF] text-white font-bold' : 'text-[#8B949E] hover:text-[#E6EDF3]'}`}
              >
                My Publications
              </button>
            )}
            {sessionStorage.getItem('token') && (
               <button 
                 onClick={() => { setView('admin_pending'); fetchPending(); }}
                 className={`px-4 py-2 transition-colors border-l border-[#202938] ${view === 'admin_pending' ? 'bg-[#FFB74D] text-[#080A0D] font-bold' : 'text-[#8B949E] hover:text-[#FFB74D]'}`}
               >
                 Admin
               </button>
            )}
          </div>
        </div>

        {error && (
          <div className="bg-[#EF5350]20 border border-[#EF5350] text-[#EF5350] p-4 rounded-lg flex items-center gap-3 font-mono text-sm">
            <AlertTriangle size={18} /> {error}
          </div>
        )}

        {/* View Router */}
        {view === 'detail' && renderDetail()}
        {view === 'my_strategies' && renderMyStrategies()}
        {view === 'admin_pending' && renderAdminPending()}
        
        {view === 'browse' && (
          <>
            {/* Search & Filter Bar */}
            <div className="bg-[#131722] border border-[#202938] rounded-xl p-3 flex flex-wrap gap-4 items-center">
              <div className="flex items-center gap-2 px-3 bg-[#080A0D] border border-[#202938] rounded-lg flex-1 min-w-[200px]">
                <Search size={16} className="text-[#8B949E]" />
                <input 
                  type="text" 
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && fetchStrategies()}
                  placeholder="Search name, description, tags..." 
                  className="bg-transparent border-none outline-none text-sm p-2 w-full font-mono text-[#E6EDF3] placeholder-[#8B949E]"
                />
              </div>
              <div className="flex gap-2">
                <select 
                  className="bg-[#080A0D] border border-[#202938] text-[#8B949E] font-mono text-xs rounded-lg px-3 py-2 outline-none"
                  value={sort}
                  onChange={(e) => { setSort(e.target.value); setPage(1); }}
                >
                  <option value="clones">Most Cloned</option>
                  <option value="rating">Highest Rated</option>
                  <option value="sharpe">Highest Sharpe</option>
                  <option value="return">Highest Return</option>
                  <option value="newest">Newest</option>
                  <option value="featured">Featured First</option>
                </select>
                <button onClick={() => fetchStrategies()} className="bg-[#00D4FF] hover:bg-[#00D4FF]90 text-white px-4 py-2 rounded-lg text-xs font-bold font-mono">
                  Apply
                </button>
              </div>
            </div>

            {/* Strategy Grid */}
            {loading ? (
              <div className="flex justify-center items-center py-20 text-[#8B949E] font-mono">
                <Activity className="animate-spin mr-2" size={20} /> Loading Marketplace...
              </div>
            ) : strategies.length === 0 ? (
              <div className="flex flex-col justify-center items-center py-20 text-[#8B949E] font-mono gap-4 border border-dashed border-[#202938] rounded-xl">
                <Search size={48} className="opacity-20" />
                No strategies found matching your criteria.
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                  {strategies.map(renderCard)}
                </div>
                
                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="flex justify-center items-center gap-4 mt-8 font-mono text-sm">
                    <button 
                      disabled={page === 1}
                      onClick={() => { setPage(p => p - 1); }}
                      className="px-4 py-2 bg-[#131722] border border-[#202938] rounded-lg hover:border-[#8B949E] disabled:opacity-50"
                    >
                      Previous
                    </button>
                    <span className="text-[#8B949E]">Page {page} of {totalPages}</span>
                    <button 
                      disabled={page === totalPages}
                      onClick={() => { setPage(p => p + 1); }}
                      className="px-4 py-2 bg-[#131722] border border-[#202938] rounded-lg hover:border-[#8B949E] disabled:opacity-50"
                    >
                      Next
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}

      </div>
    </div>
  );
};

export default StrategyMarketplace;

