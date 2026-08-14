import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, CheckCircle, Globe, MapPin, MessageCircle, ShieldCheck, TrainFront } from 'lucide-react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '@/lib/api';
import logo from '../assets/pune-metro-logo.png';

interface PublicStats {
  total_tickets_resolved_this_month: number;
  supported_languages: string[];
  station_count: number;
  tickets_resolved_per_day: { date: string; count: number }[];
}

const sevenDaySeries = (rows: PublicStats['tickets_resolved_per_day']) => {
  const byDate = new Map(rows.map(item => [item.date, item.count]));
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (6 - index));
    const key = date.toISOString().slice(0, 10);
    return { date: key, label: date.toLocaleDateString('en-IN', { weekday: 'short' }), count: byDate.get(key) || 0 };
  });
};

const PublicDashboard = () => {
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api.get('/public/stats').then(response => setStats(response.data)).catch(() => setFailed(true));
  }, []);

  const activity = useMemo(() => sevenDaySeries(stats?.tickets_resolved_per_day || []), [stats]);

  return <main className="public-shell">
    <nav className="public-nav">
      <div className="public-container nav-inner">
        <div className="public-brand"><span><img src={logo} alt="Pune Metro" /></span><div><strong>Pune Metro</strong><small>AI Passenger Assistance</small></div></div>
        <div className="nav-status"><i /> System operational</div>
        <Link to="/admin" className="public-login"><ShieldCheck /> Admin portal <ArrowRight /></Link>
      </div>
    </nav>

    <section className="public-hero">
      <div className="public-container public-hero-inner">
        <div className="public-copy">
          <p className="public-kicker"><span /> Smart support for every journey</p>
          <h1>Your Pune Metro journey,<br/><em>made simpler.</em></h1>
          <p>Instant passenger assistance in English, Hindi and Marathi—across WhatsApp chat and voice calling.</p>
          <div className="public-actions"><span><MessageCircle/> 24×7 AI assistance</span><span><TrainFront/> Metro-focused answers</span></div>
        </div>
        <div className="metro-orbit" aria-hidden="true"><div className="orbit one"/><div className="orbit two"/><div className="train-badge"><TrainFront/></div><span className="station-dot dot-one"/><span className="station-dot dot-two"/><span className="station-dot dot-three"/></div>
      </div>
    </section>

    <section className="public-container public-content">
      {failed ? <div className="public-error">Live service statistics are temporarily unavailable. Passenger assistance remains operational.</div> : !stats ? <div className="public-loading"><div/><div/><div/></div> : <>
        <div className="public-metrics">
          <PublicMetric icon={CheckCircle} value={stats.total_tickets_resolved_this_month.toString()} label="Tickets resolved" detail="This month" tone="green" />
          <PublicMetric icon={Globe} value={stats.supported_languages.length.toString()} label="Supported languages" detail={stats.supported_languages.join(' · ')} tone="violet" />
          <PublicMetric icon={MapPin} value={stats.station_count.toString()} label="Metro stations" detail="Across the active network" tone="cyan" />
        </div>

        <div className="public-grid">
          <section className="public-panel activity-card">
            <div className="public-panel-heading"><div><span>Service performance</span><h2>Tickets resolved this week</h2><p>Daily resolution activity over the last seven days.</p></div><div className="week-total"><strong>{activity.reduce((sum, item) => sum + item.count, 0)}</strong><small>resolved</small></div></div>
            {activity.every(item => item.count === 0) ? <div className="zero-chart"><div className="zero-bars">{activity.map((item, index) => <span key={item.date} style={{ height: `${20 + index * 4}%` }}/>)}</div><div><CheckCircle/><strong>All clear this week</strong><p>No resolved tickets have been recorded yet. New resolution activity will appear here automatically.</p></div></div> : <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={activity} margin={{ top: 15, right: 8, left: -25, bottom: 0 }}>
                <defs><linearGradient id="publicFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#7c3aed" stopOpacity={0.35}/><stop offset="100%" stopColor="#7c3aed" stopOpacity={0}/></linearGradient></defs>
                <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="#e8e2f1"/><XAxis dataKey="label" axisLine={false} tickLine={false}/><YAxis axisLine={false} tickLine={false} allowDecimals={false}/><Tooltip contentStyle={{ borderRadius: 14, border: '1px solid #e8e2f1' }}/><Area type="monotone" dataKey="count" stroke="#6d28d9" strokeWidth={3} fill="url(#publicFill)"/>
              </AreaChart>
            </ResponsiveContainer>}
          </section>

          <aside className="public-panel language-card"><span>Multilingual care</span><h2>Speak naturally.<br/>We understand.</h2><p>Get passenger support in the language most comfortable for you.</p><div className="language-list">{stats.supported_languages.map((language, index) => <div key={language}><i>{['EN','हि','म'][index]}</i><span><strong>{language}</strong><small>Chat and voice support</small></span><CheckCircle/></div>)}</div></aside>
        </div>
      </>}
    </section>

    <footer className="public-footer"><div className="public-container"><span>© 2026 Pune Metro passenger assistance</span><span>Powered by secure AI service operations</span></div></footer>
  </main>;
};

const PublicMetric = ({ icon: Icon, value, label, detail, tone }: { icon: typeof CheckCircle; value: string; label: string; detail: string; tone: string }) => <div className="public-metric"><div className={`public-metric-icon ${tone}`}><Icon/></div><div><strong>{value}</strong><span>{label}</span><small>{detail}</small></div></div>;

export default PublicDashboard;
