import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { format } from 'date-fns';
import {
  Activity, ArrowRight, AlertCircle, Heart, Lightbulb, HelpCircle,
  MessageSquare, RefreshCw, ShieldCheck, Ticket, Users,
} from 'lucide-react';
import {
  Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import apiClient from '../services/apiClient';
import { CategoryLog, PaginatedResponse, StatsOverview } from '../types/api';

const categoryStyle: Record<string, { label: string; color: string; icon: typeof AlertCircle; className: string }> = {
  complaint: { label: 'Complaints', color: '#ef4444', icon: AlertCircle, className: 'category-complaint' },
  enquiry: { label: 'Enquiries', color: '#3b82f6', icon: HelpCircle, className: 'category-enquiry' },
  suggestion: { label: 'Suggestions', color: '#f59e0b', icon: Lightbulb, className: 'category-suggestion' },
  appreciation: { label: 'Appreciation', color: '#10b981', icon: Heart, className: 'category-appreciation' },
};

const DashboardPage = () => {
  const [stats, setStats] = useState<StatsOverview | null>(null);
  const [recent, setRecent] = useState<CategoryLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(false);
    try {
      const [overview, logs] = await Promise.all([
        apiClient.get<StatsOverview>('/stats/overview'),
        apiClient.get<PaginatedResponse<CategoryLog>>('/category-logs?page=1&page_size=6'),
      ]);
      setStats(overview.data);
      setRecent(logs.data.items);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const categories = useMemo(() => Object.entries(categoryStyle).map(([key, meta]) => ({
    key,
    ...meta,
    value: stats?.categories_by_type[key] || 0,
  })), [stats]);

  if (loading) return <DashboardSkeleton />;
  if (error || !stats) return (
    <div className="empty-state">
      <AlertCircle className="h-9 w-9 text-danger" />
      <h2 className="text-xl font-semibold">Dashboard data is unavailable</h2>
      <p>Check the API connection and try again.</p>
      <button className="primary-button" onClick={load}><RefreshCw className="h-4 w-4" /> Retry</button>
    </div>
  );

  const pending = stats.tickets_by_status.pending || 0;
  const resolved = stats.tickets_by_status.resolved || 0;
  const ticketTotal = Object.values(stats.tickets_by_status).reduce((sum, count) => sum + count, 0);
  const resolutionRate = ticketTotal ? Math.round((resolved / ticketTotal) * 100) : 0;

  return (
    <div className="space-y-7">
      <section className="hero-panel">
        <div>
          <div className="eyebrow"><span className="live-dot" /> Live service overview</div>
          <h1>Passenger care command centre</h1>
          <p>Every chat and call, classified into the right service queue.</p>
        </div>
        <div className="hero-actions">
          <Link to="/admin/conversations" className="secondary-button"><MessageSquare className="h-4 w-4" /> View conversations</Link>
          <Link to="/admin/tickets" className="primary-button"><Ticket className="h-4 w-4" /> Manage tickets</Link>
        </div>
      </section>

      <section className="metric-grid">
        <Metric label="Passengers" value={stats.total_users} detail="Unique users" icon={Users} tone="violet" />
        <Metric label="Conversations" value={stats.total_conversations} detail={`${stats.conversations_by_channel.chat || 0} chat · ${stats.conversations_by_channel.call || 0} calls`} icon={MessageSquare} tone="blue" />
        <Metric label="Messages today" value={stats.messages_today} detail={`${stats.messages_this_week} this week`} icon={Activity} tone="cyan" />
        <Metric label="Open tickets" value={pending} detail={`${resolutionRate}% resolution rate`} icon={ShieldCheck} tone="orange" />
      </section>

      <section>
        <div className="section-heading">
          <div><span>Classification</span><h2>Passenger intent at a glance</h2></div>
          <Link to="/admin/logs">View classification records <ArrowRight className="h-4 w-4" /></Link>
        </div>
        <div className="category-grid">
          {categories.map(({ key, label, value, icon: Icon, className }) => (
            <Link to="/admin/logs" className={`category-card ${className}`} key={key}>
              <div className="category-icon"><Icon /></div>
              <div><strong>{value.toLocaleString()}</strong><span>{label}</span></div>
              <ArrowRight className="category-arrow" />
            </Link>
          ))}
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="panel chart-panel">
          <div className="panel-heading"><div><span>7-day activity</span><h2>Conversation volume</h2></div></div>
          <ResponsiveContainer width="100%" height={285}>
            <AreaChart data={stats.messages_per_day} margin={{ top: 15, right: 8, left: -22, bottom: 0 }}>
              <defs><linearGradient id="messageFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6d28d9" stopOpacity={0.4}/><stop offset="100%" stopColor="#6d28d9" stopOpacity={0}/></linearGradient></defs>
              <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="hsl(var(--border))" />
              <XAxis dataKey="date" tickFormatter={(value) => value.slice(5)} axisLine={false} tickLine={false} fontSize={12} />
              <YAxis axisLine={false} tickLine={false} fontSize={12} allowDecimals={false} />
              <Tooltip contentStyle={{ borderRadius: 14, border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))' }} />
              <Area type="monotone" dataKey="count" stroke="#7c3aed" strokeWidth={3} fill="url(#messageFill)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="panel distribution-panel">
          <div className="panel-heading"><div><span>Intent mix</span><h2>All classifications</h2></div></div>
          <div className="distribution-body">
            <ResponsiveContainer width="48%" height={210}>
              <PieChart><Pie data={categories} dataKey="value" nameKey="label" innerRadius={60} outerRadius={88} paddingAngle={3} stroke="none">
                {categories.map((item) => <Cell key={item.key} fill={item.color} />)}
              </Pie><Tooltip /></PieChart>
            </ResponsiveContainer>
            <div className="legend-list">{categories.map(item => <div key={item.key}><i style={{ background: item.color }} /><span>{item.label}</span><strong>{item.value}</strong></div>)}</div>
          </div>
        </div>
      </section>

      <section className="panel recent-panel">
        <div className="panel-heading row"><div><span>Most recent</span><h2>Classified interactions</h2></div><Link to="/admin/logs">See all <ArrowRight className="h-4 w-4" /></Link></div>
        <div className="recent-list">
          {recent.length ? recent.map(log => {
            const category = log.categories[0] || 'enquiry';
            const meta = categoryStyle[category] || categoryStyle.enquiry;
            const Icon = meta.icon;
            return <div className="recent-row" key={log.id}>
              <div className={`recent-icon ${meta.className}`}><Icon /></div>
              <div className="recent-copy"><strong>{log.user.name || log.user.whatsapp_number || 'Passenger'}</strong><p>{log.message || 'No description provided'}</p></div>
              <span className={`intent-pill ${meta.className}`}>{meta.label.replace(/s$/, '')}</span>
              <time>{format(new Date(log.created_at), 'dd/MM/yy')}</time>
            </div>;
          }) : <div className="empty-inline">No classified interactions yet.</div>}
        </div>
      </section>
    </div>
  );
};

const Metric = ({ label, value, detail, icon: Icon, tone }: { label: string; value: number; detail: string; icon: typeof Users; tone: string }) => (
  <div className="metric-card"><div className={`metric-icon ${tone}`}><Icon /></div><div><span>{label}</span><strong>{value.toLocaleString()}</strong><small>{detail}</small></div></div>
);

const DashboardSkeleton = () => <div className="space-y-6"><div className="skeleton h-40 rounded-3xl"/><div className="grid grid-cols-4 gap-5">{[1,2,3,4].map(i => <div key={i} className="skeleton h-32 rounded-2xl"/>)}</div><div className="skeleton h-80 rounded-2xl"/></div>;

export default DashboardPage;
