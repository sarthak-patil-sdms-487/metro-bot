import { useEffect, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { getPublicStats } from '@/lib/api';

interface PublicStats {
  resolved_tickets_this_month: number;
  supported_languages: string[];
  station_count: number;
  resolved_tickets_per_day: { date: string; count: number }[];
}

export function PublicDashboardPage() {
  const [stats, setStats] = useState<PublicStats | null>(null);

  useEffect(() => {
    getPublicStats().then(setStats);
  }, []);

  if (!stats) {
    return <div>Loading...</div>;
  }

  return (
    <div className="container mx-auto p-4">
      <h1 className="text-3xl font-bold mb-4">Pune Metro AI Assistant</h1>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle>Tickets Resolved This Month</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{stats.resolved_tickets_this_month}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Supported Languages</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{stats.supported_languages.join(', ')}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Stations Served</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{stats.station_count}</p>
          </CardContent>
        </Card>
      </div>
      <div className="mt-8">
        <Card>
          <CardHeader>
            <CardTitle>Tickets Resolved (Last 7 Days)</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={stats.resolved_tickets_per_day}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <Tooltip />
                <Area type="monotone" dataKey="count" stroke="#8884d8" fill="#8884d8" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
