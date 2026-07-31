import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from "react-router-dom";
import { 
  Search, Filter, TrendingUp, Users, Star, ArrowRight, Zap, 
  Shield, Activity, Cpu, Hexagon, X, Check, AlertTriangle, ArrowLeft,
  Sparkles, Flame, Trophy, Heart, Eye, DollarSign, BarChart3
} from 'lucide-react';
import { useAppState } from '../AppState';
import client, { publicGet } from '../apiClient';

const StrategyMarketplace = () => {
  const navigate = useNavigate();
  const { uiMode } = useAppState();
  const [view, setView] = useState('browse');
  
  // Data states
  const [featuredStrategies, setFeaturedStrategies] = useState([]);
  const [trendingStrategies, setTrendingStrategies] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [categories, setCategories] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  
  // Pagination & Filter state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [sort, setSort] = useState('clones');
  
  // UI states
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [cloneLoading, setCloneLoading] = useState(false);
  
  // Fetch Featured
  const fetchFeatured = useCallback(async () => {
    try {
      const token = sessionStorage.getItem('token');
      let data;
      if (token) {
        const res = await client.get('/api/library/featured?limit=3');
        data = res.data;
      } else {
        data = await publicGet('/api/library/featured?limit=3');
      }
      setFeaturedStrategies(data.items || []);
    } catch (err) {
      console.error('Failed to fetch featured:', err);
    }
  }, []);

  // Fetch Trending
  const fetchTrending = useCallback(async () => {
    try {
      const token = sessionStorage.getItem('token');
      let data;
      if (token) {
        const res = await client.get('/api/library/trending?limit=5');
        data = res.data;
      } else {
        data = await publicGet('/api/library/trending?limit=5');
      }
      setTrendingStrategies(data.items || []);
    } catch (err) {
      console.error('Failed to fetch trending:', err);
    }
  }, []);

  // Fetch Categories
  const fetchCategories = useCallback(async () => {
    try {
      const token = sessionStorage.getItem('token');
      let data;
      if (token) {
        const res = await client.get('/api/library/categories');
        data = res.data;
      } else {
        data = await publicGet('/api/library/categories');
      }
      setCategories(data.categories || []);
    } catch (err) {
      console.error('Failed to fetch categories:', err);
    }
  }, []);

  // Fetch Catalogue
  const fetchStrategies = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { page, limit: 20, sort };
      if (searchQuery) params.q = searchQuery;
      if (selectedCategory) params.category = selectedCategory;
      
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
  }, [page, sort, searchQuery, selectedCategory]);

  // Initial load
  useEffect(() => {
    if (view === 'browse') {
      fetchFeatured();
      fetchTrending();
      fetchCategories();
      fetchStrategies();
    }
  }, [view, fetchFeatured, fetchTrending, fetchCategories, fetchStrategies]);

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
      alert(res.data.message || 'Strategy cloned successfully!');
      navigate("/app/builder");
    } catch (err) {
      console.error(err);
      alert(err.message || 'Failed to clone strategy.');
    } finally {
      setCloneLoading(false);
    }
  };

  // Subscribe Flow
  const handleSubscribe = async (strat) => {
    try {
      const res = await client.post(`/api/library/${strat.id}/checkout`, { currency: 'USD' });
      if (res.data.checkout_url) {
        // Redirect to Stripe checkout
        window.location.href = res.data.checkout_url;
      } else if (res.data.order_id) {
        // Razorpay - would need Razorpay SDK integration
        alert(`Razorpay order created: ${res.data.order_id}. Razorpay SDK integration required.`);
      }
    } catch (err) {
      console.error(err);
      alert(err.response?.data?.detail || err.message || 'Failed to create checkout session.');
    }
  };

  const renderFeaturedCard = (strat) => (
    <div 
      key={strat.id} 
      onClick={() => loadDetail(strat.id)}
      className="relative bg-gradient-to-br from-[#131722] to-[#0d1117] border border-[#00D4FF]30 rounded-2xl p-6 cursor-pointer group hover:border-[#00D4FF] transition-all overflow-hidden"
    >
      <div className="absolute top-0 right-0 bg-gradient-to-l from-[#00D4FF] to-transparent w-32 h-32 opacity-10 group-hover:opacity-20 transition-opacity" />
      <div className="absolute top-3 right-3 bg-[#FFB74D] text-[#080A0D] text-[10px] font-bold px-3 py-1 rounded-full font-mono uppercase flex items-center gap-1">
        <Sparkles size={12} /> Featured
      </div>
      <div className="relative z-10">
        <div className="flex items-start gap-4">
          <div className="w-16 h-16 rounded-xl bg-[#080A0D] border border-[#202938] flex items-center justify-center">
            <Cpu className="text-[#00D4FF]" size={32} />
          </div>
          <div className="flex-1">
            <h3 className="font-bold text-xl text-[#E6EDF3] group-hover:text-[#00D4FF] transition-colors">{strat.name}</h3>
            <div className="text-[#8B949E] text-xs font-mono mt-1">by <span className="text-[#00D4FF] font-bold">{strat.author_alias || 'Unknown'}</span></div>
          </div>
        </div>
        <div className="grid grid-cols-4 gap-3 mt-6">
          <div className="bg-[#080A0D] p-3 rounded-lg border border-[#202938]">
            <div className="text-[#8B949E] text-[9px] font-mono uppercase">Sharpe</div>
            <div className="text-[#FFB74D] font-bold font-mono text-lg">{(strat.backtest_sharpe_ratio || 0).toFixed(2)}</div>
          </div>
          <div className="bg-[#080A0D] p-3 rounded-lg border border-[#202938]">
            <div className="text-[#8B949E] text-[9px] font-mono uppercase">Return</div>
            <div className={`font-bold font-mono text-lg ${strat.backtest_total_return_pct >= 0 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
              {(strat.backtest_total_return_pct || 0).toFixed(1)}%
            </div>
          </div>
          <div className="bg-[#080A0D] p-3 rounded-lg border border-[#202938]">
            <div className="text-[#8B949E] text-[9px] font-mono uppercase">Subs</div>
            <div className="text-[#E6EDF3] font-bold font-mono text-lg">{strat.subscriber_count || strat.clone_count || 0}</div>
          </div>
          <div className="bg-[#080A0D] p-3 rounded-lg border border-[#202938]">
            <div className="text-[#8B949E] text-[9px] font-mono uppercase">Price</div>
            <div className="text-[#00D4FF] font-bold font-mono text-lg">
              {strat.price ? `$${strat.price}` : 'Free'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  const renderCard = (strat) => (
    <div 
      key={strat.id} 
      onClick={() => loadDetail(strat.id)}
      className="bg-[#131722] border border-[#202938] rounded-xl p-5 cursor-pointer group hover:border-[#00D4FF]50 transition-all relative overflow-hidden"
    >
      {strat.is_featured && (
        <div className="absolute top-0 right-0 bg-[#FFB74D] text-[#080A0D] text-[9px] font-bold px-2 py-0.5 rounded-bl font-mono uppercase">
          Featured
        </div>
      )}
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="font-bold text-[#E6EDF3] group-hover:text-[#00D4FF] transition-colors">{strat.name}</h3>
          <div className="text-[#8B949E] text-xs font-mono mt-1">by <span className="text-[#00D4FF] font-bold">{strat.author_alias || 'Unknown'}</span></div>
        </div>
        <span className="bg-[#080A0D] border border-[#202938] px-2 py-1 rounded text-[10px] font-mono text-[#8B949E] uppercase">
          {strat.category}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 py-4 border-y border-[#202938]">
        <div className="flex flex-col gap-1">
          <span className="text-[#8B949E] text-[9px] font-mono uppercase flex items-center gap-1"><Users size={10}/> Subs</span>
          <span className="text-[#E6EDF3] font-bold font-mono">{strat.subscriber_count || strat.clone_count || 0}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[#8B949E] text-[9px] font-mono uppercase flex items-center gap-1"><TrendingUp size={10}/> Return</span>
          <span className={`font-bold font-mono ${strat.backtest_total_return_pct >= 0 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
            {(strat.backtest_total_return_pct || 0).toFixed(1)}%
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[#8B949E] text-[9px] font-mono uppercase flex items-center gap-1"><Activity size={10}/> Sharpe</span>
          <span className="text-[#FFB74D] font-bold font-mono">{(strat.backtest_sharpe_ratio || 0).toFixed(2)}</span>
        </div>
      </div>

      <div className="flex justify-between items-center mt-4">
        <span className="text-[10px] font-mono font-bold flex items-center gap-1 text-[#FFB74D]">
          <Star size={10} fill="currentColor" /> {strat.avg_rating ? strat.avg_rating.toFixed(1) : 'New'}
        </span>
        <span className="text-[#00D4FF] font-bold font-mono">
          {strat.price ? `$${strat.price}/mo` : 'Free'}
        </span>
      </div>
    </div>
  );

  const renderDetail = () => {
    if (!selectedStrategy) return null;
    const strat = selectedStrategy;
    
    return (
      <div className="flex flex-col gap-6">
        <button onClick={() => setView('browse')} className="flex items-center gap-2 text-[#8B949E] hover:text-[#E6EDF3] transition-colors w-max font-mono text-sm">
          <ArrowLeft size={16} /> Back to Marketplace
        </button>
        
        <div className="bg-[#131722] border border-[#202938] rounded-2xl p-8 shadow-lg flex flex-col gap-8">
          {/* Header */}
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-3">
                {strat.is_featured && (
                  <span className="bg-[#FFB74D] text-[#080A0D] text-[10px] font-bold px-3 py-1 rounded-full font-mono uppercase flex items-center gap-1">
                    <Sparkles size={12} /> Featured
                  </span>
                )}
                <span className="bg-[#080A0D] border border-[#202938] px-3 py-1 rounded text-[10px] font-mono text-[#8B949E] uppercase">
                  {strat.category}
                </span>
                <span className="bg-[#080A0D] border border-[#202938] px-3 py-1 rounded text-[10px] font-mono text-[#8B949E] uppercase">
                  {strat.difficulty}
                </span>
              </div>
              <h1 className="text-4xl font-black tracking-tight text-[#E6EDF3] mb-2">{strat.name}</h1>
              <div className="text-[#8B949E] font-mono text-sm">Created by <span className="text-[#00D4FF] font-bold">{strat.author_alias || 'Unknown'}</span></div>
            </div>
            <div className="flex flex-col gap-3 items-end">
              <div className="text-right">
                <div className="text-[#8B949E] text-xs font-mono uppercase mb-1">Monthly Price</div>
                <div className="text-3xl font-black text-[#00D4FF] font-mono">
                  {strat.price ? `$${strat.price}` : 'Free'}
                </div>
              </div>
              <button 
                onClick={() => strat.price ? handleSubscribe(strat) : handleClone(strat)}
                disabled={cloneLoading}
                className={`px-8 py-3 rounded-xl text-sm font-bold font-mono flex items-center gap-2 transition-colors ${
                  strat.price 
                    ? 'bg-[#26A69A] hover:bg-[#26A69A]90 text-white shadow-[0_0_20px_rgba(38,166,154,0.3)]' 
                    : 'bg-[#00D4FF] hover:bg-[#00D4FF]90 text-white shadow-[0_0_20px_rgba(0,212,255,0.3)]'
                }`}
              >
                {strat.price ? 'Subscribe' : 'Clone Free'} <ArrowRight size={16} />
              </button>
            </div>
          </div>
          
          <div className="text-[#C9D1D9] leading-relaxed max-w-4xl text-sm">
            {strat.description || 'No description provided.'}
          </div>
          
          {/* Performance Metrics */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 py-6 border-y border-[#202938]">
            <div className="flex flex-col gap-2 bg-[#080A0D] p-4 rounded-xl border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><TrendingUp size={12}/> Total Return</span>
              <span className={`text-2xl font-bold font-mono ${strat.backtest_total_return_pct >= 0 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
                {strat.backtest_total_return_pct > 0 ? '+' : ''}{(strat.backtest_total_return_pct || 0).toFixed(2)}%
              </span>
            </div>
            <div className="flex flex-col gap-2 bg-[#080A0D] p-4 rounded-xl border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><Activity size={12}/> Sharpe Ratio</span>
              <span className="text-2xl font-bold font-mono text-[#FFB74D]">{(strat.backtest_sharpe_ratio || 0).toFixed(2)}</span>
            </div>
            <div className="flex flex-col gap-2 bg-[#080A0D] p-4 rounded-xl border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><Shield size={12}/> Win Rate</span>
              <span className="text-2xl font-bold font-mono text-[#E6EDF3]">{(strat.backtest_win_rate_pct || 0).toFixed(1)}%</span>
            </div>
            <div className="flex flex-col gap-2 bg-[#080A0D] p-4 rounded-xl border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><AlertTriangle size={12}/> Max Drawdown</span>
              <span className="text-2xl font-bold font-mono text-[#EF5350]">{(strat.backtest_max_drawdown_pct || 0).toFixed(2)}%</span>
            </div>
            <div className="flex flex-col gap-2 bg-[#080A0D] p-4 rounded-xl border border-[#202938]">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1"><BarChart3 size={12}/> Profit Factor</span>
              <span className="text-2xl font-bold font-mono text-[#E6EDF3]">{(strat.backtest_profit_factor || 0).toFixed(2)}</span>
            </div>
          </div>
          
          {/* Evaluation Score */}
          {strat.evaluation_score && (
            <div className="bg-[#080A0D] border border-[#202938] p-4 rounded-xl flex items-center gap-4">
              <div className="w-16 h-16 rounded-full bg-gradient-to-br from-[#00D4FF] to-[#26A69A] flex items-center justify-center">
                <span className="text-2xl font-black text-white font-mono">{strat.evaluation_score.toFixed(0)}</span>
              </div>
              <div>
                <div className="text-[#E6EDF3] font-bold">Evaluation Score</div>
                <div className="text-[#8B949E] text-xs font-mono">Based on Sharpe, Return, Win Rate, and Profit Factor</div>
              </div>
            </div>
          )}
          
          {/* Tags */}
          {strat.tags && strat.tags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {strat.tags.map(tag => (
                <span key={tag} className="text-[10px] border border-[#202938] bg-[#1A222C] text-[#8B949E] px-3 py-1 rounded-full font-mono uppercase">
                  {tag}
                </span>
              ))}
            </div>
          )}
          
          {/* Stats */}
          <div className="grid grid-cols-3 gap-4 pt-6 border-t border-[#202938]">
            <div className="flex flex-col gap-1">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase">Subscribers</span>
              <span className="text-xl font-bold font-mono text-[#E6EDF3]">{strat.subscriber_count || strat.clone_count || 0}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase">Rating</span>
              <span className="text-xl font-bold font-mono text-[#FFB74D] flex items-center gap-2">
                <Star size={16} fill="currentColor" /> {strat.avg_rating ? strat.avg_rating.toFixed(1) : 'New'}
                <span className="text-[#8B949E] font-normal text-sm">({strat.rating_count || 0})</span>
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[#8B949E] text-[10px] font-mono uppercase">Published</span>
              <span className="text-xl font-bold font-mono text-[#E6EDF3]">
                {strat.published_at ? new Date(strat.published_at).toLocaleDateString() : '—'}
              </span>
            </div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-y-auto bg-[#08090c] text-[#e2e8f0] font-['Inter']">
      <div className="max-w-7xl mx-auto p-6 flex flex-col gap-8">
        
        {/* Hero Section */}
        <div className="relative bg-gradient-to-br from-[#131722] to-[#0d1117] border border-[#202938] rounded-2xl p-8 overflow-hidden">
          <div className="absolute top-0 right-0 w-96 h-96 bg-[#00D4FF]5 rounded-full blur-3xl" />
          <div className="relative z-10">
            <div className="flex items-center gap-3 mb-4">
              <Hexagon className="text-[#00D4FF]" size={32} />
              <h1 className="text-4xl font-black tracking-tight text-[#E6EDF3]">Strategy Marketplace</h1>
            </div>
            <p className="text-[#8B949E] font-mono text-sm max-w-2xl mb-6">
              Discover institutional-grade algorithms from top traders. One-click deploy directly to your portfolio.
            </p>
            <div className="flex gap-4">
              <div className="flex items-center gap-2 text-[#26A69A] font-mono text-sm">
                <Trophy size={16} /> {featuredStrategies.length} Featured
              </div>
              <div className="flex items-center gap-2 text-[#FFB74D] font-mono text-sm">
                <Flame size={16} /> {trendingStrategies.length} Trending
              </div>
              <div className="flex items-center gap-2 text-[#00D4FF] font-mono text-sm">
                <Users size={16} /> {strategies.length} Strategies
              </div>
            </div>
          </div>
        </div>

        {error && (
          <div className="bg-[#EF5350]20 border border-[#EF5350] text-[#EF5350] p-4 rounded-lg flex items-center gap-3 font-mono text-sm">
            <AlertTriangle size={18} /> {error}
          </div>
        )}

        {/* Featured Section */}
        {featuredStrategies.length > 0 && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <Sparkles className="text-[#FFB74D]" size={20} />
              <h2 className="text-xl font-bold font-mono">Featured Strategies</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {featuredStrategies.map(renderFeaturedCard)}
            </div>
          </div>
        )}

        {/* Search & Filter Bar */}
        <div className="bg-[#131722] border border-[#202938] rounded-xl p-4 flex flex-wrap gap-4 items-center">
          <div className="flex items-center gap-2 px-4 bg-[#080A0D] border border-[#202938] rounded-lg flex-1 min-w-[250px]">
            <Search size={16} className="text-[#8B949E]" />
            <input 
              type="text" 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchStrategies()}
              placeholder="Search strategies..." 
              className="bg-transparent border-none outline-none text-sm p-2 w-full font-mono text-[#E6EDF3] placeholder-[#8B949E]"
            />
          </div>
          
          {/* Category Pills */}
          <div className="flex gap-2 flex-wrap">
            <button 
              onClick={() => setSelectedCategory(null)}
              className={`px-3 py-1.5 rounded-full text-xs font-mono transition-colors ${
                selectedCategory === null 
                  ? 'bg-[#00D4FF] text-white' 
                  : 'bg-[#080A0D] border border-[#202938] text-[#8B949E] hover:text-[#E6EDF3]'
              }`}
            >
              All
            </button>
            {categories.slice(0, 6).map(cat => (
              <button
                key={cat.name}
                onClick={() => setSelectedCategory(cat.name)}
                className={`px-3 py-1.5 rounded-full text-xs font-mono transition-colors ${
                  selectedCategory === cat.name 
                    ? 'bg-[#00D4FF] text-white' 
                    : 'bg-[#080A0D] border border-[#202938] text-[#8B949E] hover:text-[#E6EDF3]'
                }`}
              >
                {cat.name} ({cat.count})
              </button>
            ))}
          </div>

          <select 
            className="bg-[#080A0D] border border-[#202938] text-[#8B949E] font-mono text-xs rounded-lg px-4 py-2 outline-none"
            value={sort}
            onChange={(e) => { setSort(e.target.value); setPage(1); }}
          >
            <option value="clones">Most Popular</option>
            <option value="rating">Highest Rated</option>
            <option value="sharpe">Highest Sharpe</option>
            <option value="return">Highest Return</option>
            <option value="newest">Newest</option>
          </select>
        </div>

        {/* View Router */}
        {view === 'detail' && renderDetail()}
        
        {view === 'browse' && (
          <>
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
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
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

