import { useEffect, useState } from 'react';
import apiClient from '../services/apiClient';
import { DataTable } from '../components/ui/DataTable';
import { ColumnDef } from '@tanstack/react-table';
import { format } from 'date-fns';
import toast from 'react-hot-toast';
import Modal from '../components/ui/Modal';
import StatusBadge from '../components/ui/StatusBadge';
import { CheckCircle, Clock, Copy, Eye, ShieldCheck, MessagesSquare, Ticket as TicketIcon } from 'lucide-react';
import { Message, PaginatedResponse, StatsOverview, Ticket } from '../types/api';
import { TableSkeleton } from '../components/ui/Skeleton';

const TicketsPage = () => {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [pageSize] = useState(20);
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);
  const [channel, setChannel] = useState<'all' | 'chat' | 'call'>('all');
  const [conversationMessages, setConversationMessages] = useState<Message[]>([]);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [summary, setSummary] = useState({ total: 0, pending: 0, approved: 0, resolved: 0 });

  useEffect(() => {
    fetchTickets();
  }, [page, pageSize, channel]);

  const fetchTickets = async () => {
    setLoading(true);
    try {
      const channelQuery = channel === 'all' ? '' : `&channel=${channel}`;
      const [response, statsResponse] = await Promise.all([
        apiClient.get<PaginatedResponse<Ticket>>(`/tickets?page=${page}&page_size=${pageSize}${channelQuery}`),
        apiClient.get<StatsOverview>('/stats/overview'),
      ]);
      setTickets(response.data.items);
      setTotal(response.data.total);
      const statuses = statsResponse.data.tickets_by_status;
      setSummary({
        total: Object.values(statuses).reduce((sum, count) => sum + count, 0),
        pending: statuses.pending || 0,
        approved: statuses.approved || 0,
        resolved: statuses.resolved || 0,
      });
    } catch (error) {
      console.error("Failed to fetch tickets", error);
    } finally {
      setLoading(false);
    }
  };

  const handleStatusChange = async (ticketId: number, newStatus: string) => {
    try {
      const response = await apiClient.patch<Ticket>(`/tickets/${ticketId}`, { status: newStatus });
      const updatedTicket = response.data;
      
      setTickets(prev => prev.map(t => t.id === ticketId ? updatedTicket : t));
      if (selectedTicket && selectedTicket.id === ticketId) {
        setSelectedTicket(updatedTicket);
      }
      toast.success(`Ticket #${ticketId} status updated to ${newStatus}`);
    } catch (error) {
      toast.error('Failed to update ticket status.');
      console.error("Failed to update status", error);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('Message copied to clipboard!');
  };

  const openTicket = async (ticket: Ticket) => {
    setSelectedTicket(ticket);
    setConversationLoading(true);
    setConversationMessages([]);
    try {
      const response = await apiClient.get<Message[]>(`/conversations/${ticket.conversation_id}/messages`);
      setConversationMessages(response.data);
    } finally {
      setConversationLoading(false);
    }
  };

  const columns: ColumnDef<Ticket>[] = [
    {
      id: 'ticket_ids',
      header: 'Ticket / Category ID',
      size: 130,
      cell: ({ row }) => (
        <div className="leading-tight">
          <strong className="block whitespace-nowrap text-primary">{row.original.tracking_id}</strong>
          <span className="mt-1 block whitespace-nowrap text-[11px] text-foreground/45">Category #{row.original.category_log_id}</span>
        </div>
      ),
    },
    { 
      accessorKey: 'user', 
      header: 'User',
      size: 105,
      cell: ({ row }) => <span className="block truncate font-medium">{row.original.user.name || row.original.user.whatsapp_number}</span>
    },
    { 
      accessorKey: 'categories', 
      header: 'Category',
      size: 95,
      cell: ({ row }) => <span className="capitalize">{row.original.categories.join(', ')}</span>
    },
    { 
      accessorKey: 'message.description', 
      header: 'Description',
      size: 250,
      cell: ({ row }) => <p className="truncate" title={row.original.message.description}>{row.original.message.description}</p>
    },
    { 
      accessorKey: 'status', 
      header: 'Status',
      size: 105,
      cell: ({ row }) => <StatusBadge status={row.original.status} />
    },
    { accessorKey: 'channel', header: 'Channel', size: 75, cell: ({ row }) => <span className="inline-flex rounded-full border px-2 py-1 text-xs capitalize">{row.original.channel}</span> },
    { 
      accessorKey: 'created_at', 
      header: 'Created At',
      size: 90,
      cell: ({ row }) => <span className="whitespace-nowrap">{format(new Date(row.original.created_at), 'dd/MM/yy')}</span>
    },
    {
      id: 'view', header: 'View', size: 55,
      cell: ({ row }) => <button onClick={() => openTicket(row.original)} className="icon-button mx-auto" title="View ticket and conversation" aria-label="View ticket and conversation"><Eye/></button>,
    },
  ];

  return (
    <div className="space-y-6">
      <div className="hero-panel !p-7">
        <div><div className="eyebrow"><span className="live-dot" /> Resolution queue</div><h1 className="!text-3xl">Service tickets</h1><p>Track and resolve registered complaints and passenger suggestions.</p></div>
        <TicketIcon className="relative z-10 hidden h-14 w-14 text-white/25 md:block" />
      </div>
      <div className="call-summary">
        <div><TicketIcon/><span><strong>{summary.total}</strong>Total tickets</span></div>
        <div><Clock/><span><strong>{summary.pending}</strong>Pending review</span></div>
        <div><ShieldCheck/><span><strong>{summary.approved}</strong>Approved</span></div>
        <div><CheckCircle/><span><strong>{summary.resolved}</strong>Resolved</span></div>
      </div>
      <div className="flex items-center justify-end gap-4">
        <select
          value={channel}
          onChange={(event) => { setChannel(event.target.value as 'all' | 'chat' | 'call'); setPage(1); }}
          className="rounded-md border bg-background px-3 py-2"
        >
          <option value="all">All channels</option>
          <option value="chat">Chat</option>
          <option value="call">Call</option>
        </select>
      </div>
      {loading ? (
        <TableSkeleton rows={10} cells={7} />
      ) : (
        <>
          <DataTable columns={columns} data={tickets} fixed />
          <div className="flex items-center justify-end space-x-2 py-4">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-4 py-2 border rounded-md disabled:opacity-50"
            >
              Previous
            </button>
            <span className="text-sm">
              Page {page} of {Math.ceil(total / pageSize)}
            </span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={page * pageSize >= total}
              className="px-4 py-2 border rounded-md disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </>
      )}

      <Modal isOpen={!!selectedTicket} onClose={() => setSelectedTicket(null)} title={`Ticket #${selectedTicket?.id}`}>
        {selectedTicket && (
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold">User</h3>
              <p>{selectedTicket.user.name} ({selectedTicket.user.whatsapp_number})</p>
            </div>
            <div>
              <h3 className="font-semibold">Full Message</h3>
              <div className="relative mt-1 p-3 bg-background rounded-md border max-h-60 overflow-y-auto">
                <p className="text-sm whitespace-pre-wrap">{`Name: ${selectedTicket.message.name}\nContact: ${selectedTicket.message.contact}\nStation: ${selectedTicket.message.station}\nDescription: ${selectedTicket.message.description}`}</p>
                <button 
                  onClick={() => copyToClipboard(JSON.stringify(selectedTicket.message, null, 2))} 
                  className="absolute top-2 right-2 p-1.5 rounded-md bg-card hover:bg-accent"
                >
                  <Copy className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div>
              <h3 className="font-semibold">Status</h3>
              <select
                value={selectedTicket.status}
                onChange={(e) => handleStatusChange(selectedTicket.id, e.target.value)}
                className="mt-1 w-full p-2 border rounded-md bg-background"
              >
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="resolved">Resolved</option>
                <option value="rejected">Rejected</option>
              </select>
            </div>
            <div>
              <h3 className="font-semibold">Details</h3>
              <p><strong>Tracking ID:</strong> {selectedTicket.tracking_id}</p>
              <p><strong>Ticket database ID:</strong> #{selectedTicket.id}</p>
              <p><strong>Category log ID:</strong> #{selectedTicket.category_log_id}</p>
              <p><strong>Conversation ID:</strong> #{selectedTicket.conversation_id}</p>
              <p><strong>Category:</strong> {selectedTicket.categories.join(', ')}</p>
              <p><strong>Language:</strong> {selectedTicket.language || 'Unknown'}</p>
              <p><strong>Created:</strong> {format(new Date(selectedTicket.created_at), 'dd/MM/yy, HH:mm')}</p>
            </div>
            <div>
              <h3 className="flex items-center gap-2 font-semibold"><MessagesSquare className="h-4 w-4 text-primary"/> Complete conversation</h3>
              <div className="audit-transcript mt-2">
                {conversationLoading ? <div>Loading conversation…</div> : conversationMessages.map(message => <div key={message.id} className={message.role}><div><span>{message.role === 'user' ? 'Passenger' : 'Metro AI'} · Message #{message.id}</span><p>{message.content}</p><time>{format(new Date(message.created_at), 'dd/MM/yy, HH:mm')}</time></div></div>)}
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default TicketsPage;
