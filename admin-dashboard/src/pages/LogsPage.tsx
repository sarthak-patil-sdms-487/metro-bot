import { useEffect, useState } from 'react';
import apiClient from '../services/apiClient';
import { DataTable } from '../components/ui/DataTable';
import { ColumnDef } from '@tanstack/react-table';
import { format } from 'date-fns';
import Modal from '../components/ui/Modal';
import StatusBadge from '../components/ui/StatusBadge';
import { Copy, Eye, MessagesSquare, Search, Tags } from 'lucide-react';
import { CategoryLog, Message, PaginatedResponse } from '../types/api';
import toast from 'react-hot-toast';
import { TableSkeleton } from '../components/ui/Skeleton';

const LogsPage = () => {
  const [logs, setLogs] = useState<CategoryLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [pageSize] = useState(50);
  const [selectedLog, setSelectedLog] = useState<CategoryLog | null>(null);
  const [category, setCategory] = useState('all');
  const [search, setSearch] = useState('');
  const [conversationMessages, setConversationMessages] = useState<Message[]>([]);
  const [conversationLoading, setConversationLoading] = useState(false);

  useEffect(() => {
    const fetchLogs = async () => {
      setLoading(true);
      try {
        const response = await apiClient.get<PaginatedResponse<CategoryLog>>(`/category-logs?page=${page}&page_size=${pageSize}`);
        setLogs(response.data.items);
        setTotal(response.data.total);
      } catch (error) {
        console.error("Failed to fetch logs", error);
      } finally {
        setLoading(false);
      }
    };
    fetchLogs();
  }, [page, pageSize]);
  
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('Message copied to clipboard!');
  };

  const openLog = async (log: CategoryLog) => {
    setSelectedLog(log);
    setConversationLoading(true);
    setConversationMessages([]);
    try {
      const response = await apiClient.get<Message[]>(`/conversations/${log.conversation_id}/messages`);
      setConversationMessages(response.data);
    } finally {
      setConversationLoading(false);
    }
  };

  const columns: ColumnDef<CategoryLog>[] = [
    { accessorKey: 'id', header: 'Category ID', size: 90, cell: ({ row }) => <strong>#{row.original.id}</strong> },
    { accessorKey: 'conversation_id', header: 'Conversation', size: 95, cell: ({ row }) => `#${row.original.conversation_id}` },
    { 
      accessorKey: 'user', 
      header: 'User',
      size: 105,
      cell: ({ row }) => <span className="block truncate">{row.original.user.name || row.original.user.whatsapp_number}</span>
    },
    { 
      accessorKey: 'categories', 
      header: 'Categories',
      size: 90,
      cell: ({ row }) => <span className="block truncate capitalize">{row.original.categories.join(', ')}</span>
    },
    { 
      accessorKey: 'message', 
      header: 'Message',
      size: 220,
      cell: ({ row }) => <p className="truncate" title={row.original.message}>{row.original.message}</p>
    },
    { 
      accessorKey: 'status', 
      header: 'Outcome',
      size: 105,
      cell: ({ row }) => <StatusBadge status={row.original.workflow_status} />
    },
    { accessorKey: 'channel', header: 'Channel', size: 70 },
    { accessorKey: 'language', header: 'Language', size: 80, cell: ({ row }) => <span className="block truncate">{row.original.language || 'Unknown'}</span> },
    { accessorKey: 'tracking_id', header: 'Tracking ID', size: 105, cell: ({ row }) => <span className="block truncate" title={row.original.tracking_id || undefined}>{row.original.tracking_id || '—'}</span> },
    { 
      accessorKey: 'created_at', 
      header: 'Timestamp',
      size: 85,
      cell: ({ row }) => format(new Date(row.original.created_at), 'dd/MM/yy')
    },
    { id: 'view', header: 'View', size: 55, cell: ({ row }) => <button onClick={() => openLog(row.original)} className="icon-button mx-auto" title="View message and conversation" aria-label={`View message for category ${row.original.id}`}><Eye/></button> },
  ];

  const visibleLogs = logs.filter((log) => {
    const matchesCategory = category === 'all' || log.categories.includes(category);
    const needle = search.trim().toLowerCase();
    const matchesSearch = !needle || log.message.toLowerCase().includes(needle)
      || (log.user.name || '').toLowerCase().includes(needle)
      || (log.user.whatsapp_number || '').includes(needle);
    return matchesCategory && matchesSearch;
  });

  return (
    <div className="space-y-6">
      <div className="hero-panel !p-7">
        <div><div className="eyebrow"><span className="live-dot" /> Intent intelligence</div><h1 className="!text-3xl">Classified interactions</h1><p>Review every complaint, enquiry, suggestion and appreciation.</p></div>
        <Tags className="relative z-10 hidden h-14 w-14 text-white/25 md:block" />
      </div>
      <div className="panel flex flex-col gap-3 md:flex-row md:items-center">
        <div className="input-shell !mt-0 flex-1"><Search/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search passenger or message…" /></div>
        <select value={category} onChange={(event) => setCategory(event.target.value)} className="h-12 rounded-xl border bg-background px-4 text-sm font-medium">
          <option value="all">All classifications</option><option value="complaint">Complaints</option><option value="enquiry">Enquiries</option><option value="suggestion">Suggestions</option><option value="appreciation">Appreciation</option>
        </select>
      </div>
      <div className="rounded-2xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100">
        <strong>Five chat menu choices:</strong> Complaint, Suggestion, Appreciation, Enquiry and Other help. “Other help” is stored as <strong>Enquiry</strong>, so reporting remains consistent across four canonical classifications.
      </div>
      {loading ? (
        <TableSkeleton rows={15} cells={7} />
      ) : (
        <>
          <DataTable columns={columns} data={visibleLogs} fixed />
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

      <Modal isOpen={!!selectedLog} onClose={() => setSelectedLog(null)} title={`Log #${selectedLog?.id}`}>
        {selectedLog && (
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold">User</h3>
              <p>{selectedLog.user.name} ({selectedLog.user.whatsapp_number})</p>
            </div>
            <div>
              <h3 className="font-semibold">Full Message</h3>
              <div className="relative mt-1 p-3 bg-background rounded-md border max-h-60 overflow-y-auto">
                <p className="text-sm whitespace-pre-wrap">{selectedLog.message}</p>
                <button 
                  onClick={() => copyToClipboard(selectedLog.message)} 
                  className="absolute top-2 right-2 p-1.5 rounded-md bg-card hover:bg-accent"
                >
                  <Copy className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div>
              <h3 className="font-semibold">Details</h3>
              <p><strong>Categories:</strong> {selectedLog.categories.join(', ')}</p>
              <p><strong>Category log ID:</strong> #{selectedLog.id}</p>
              <p><strong>Conversation ID:</strong> #{selectedLog.conversation_id}</p>
              <p><strong>Tracking ID:</strong> {selectedLog.tracking_id || 'Not applicable'}</p>
              <p><strong>Channel:</strong> {selectedLog.channel}</p>
              <p><strong>Language:</strong> {selectedLog.language || 'Unknown'}</p>
              <p><strong>Subcategory:</strong> {selectedLog.subcategory || 'N/A'}</p>
              <p><strong>Outcome:</strong> <StatusBadge status={selectedLog.workflow_status} /></p>
              <p><strong>Timestamp:</strong> {format(new Date(selectedLog.created_at), 'dd/MM/yy, HH:mm')}</p>
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

export default LogsPage;
