import { useEffect, useState } from 'react';
import apiClient from '../services/apiClient';
import { DataTable } from '../components/ui/DataTable';
import { ColumnDef } from '@tanstack/react-table';
import { format } from 'date-fns';
import { Clock, Eye, FileAudio, Hash, Languages, MessageCircle, MessageSquare, MessagesSquare, Mic, Phone, PhoneCall, Tag, Ticket } from 'lucide-react';
import Modal from '../components/ui/Modal';
import { CallSession, Conversation, Message, PaginatedResponse, StatsOverview } from '../types/api';
import { TableSkeleton } from '../components/ui/Skeleton';

const ConversationsPage = () => {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [pageSize] = useState(20);
  const [selectedConversation, setSelectedConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [channel, setChannel] = useState<'all' | 'chat' | 'call'>('all');
  const [callsByConversation, setCallsByConversation] = useState<Record<number, CallSession>>({});
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null);
  const [summary, setSummary] = useState({ interactions: 0, chats: 0, calls: 0, recordings: 0 });

  useEffect(() => {
    const fetchConversations = async () => {
      setLoading(true);
      try {
        const channelQuery = channel === 'all' ? '' : `&channel=${channel}`;
        const [response, callsResponse, statsResponse] = await Promise.all([
          apiClient.get<PaginatedResponse<Conversation>>(`/conversations?page=${page}&page_size=${pageSize}${channelQuery}`),
          apiClient.get<PaginatedResponse<CallSession>>('/call-sessions?page_size=100'),
          apiClient.get<StatsOverview>('/stats/overview'),
        ]);
        setConversations(response.data.items);
        setTotal(response.data.total);
        setCallsByConversation(Object.fromEntries(callsResponse.data.items.map(call => [call.conversation_id, call])));
        setSummary({
          interactions: statsResponse.data.total_conversations,
          chats: statsResponse.data.conversations_by_channel.chat || 0,
          calls: statsResponse.data.conversations_by_channel.call || callsResponse.data.total,
          recordings: callsResponse.data.items.filter(call => call.recording_available).length,
        });
      } catch (error) {
        console.error("Failed to fetch conversations", error);
      } finally {
        setLoading(false);
      }
    };
    fetchConversations();
  }, [page, pageSize, channel]);

  const viewMessages = async (conversation: Conversation) => {
    setSelectedConversation(conversation);
    setMessagesLoading(true);
    try {
      if (recordingUrl) URL.revokeObjectURL(recordingUrl);
      setRecordingUrl(null);
      const call = callsByConversation[conversation.id];
      const [response, recording] = await Promise.all([
        apiClient.get<Message[]>(`/conversations/${conversation.id}/messages`),
        call?.recording_url ? apiClient.get<Blob>(call.recording_url, { responseType: 'blob' }) : Promise.resolve(null),
      ]);
      setMessages(response.data);
      if (recording) setRecordingUrl(URL.createObjectURL(recording.data));
    } catch (error) {
      console.error("Failed to fetch messages", error);
    } finally {
      setMessagesLoading(false);
    }
  };

  const columns: ColumnDef<Conversation>[] = [
    { 
      accessorKey: 'user', 
      header: 'User',
      cell: ({ row }) => (
        <div>
          <div>{row.original.user.name || 'Unknown'}</div>
          <div className="text-xs text-foreground/60">{row.original.user.whatsapp_number}</div>
        </div>
      )
    },
    {
      accessorKey: 'channel',
      header: 'Channel',
      cell: ({ row }) => (
        <span className="rounded-full border px-2 py-1 text-xs capitalize">{row.original.channel}</span>
      ),
    },
    {
      accessorKey: 'preferred_language', header: 'Language',
      cell: ({ row }) => <span className="language-chip"><Languages/>{row.original.preferred_language || row.original.detected_languages.join(', ') || 'Unknown'}</span>,
    },
    {
      accessorKey: 'categories', header: 'Classifications',
      cell: ({ row }) => <div className="category-mini-list">{row.original.categories.length ? row.original.categories.map(item => <span key={item}>{item}</span>) : <span>Unclassified</span>}</div>,
    },
    { accessorKey: 'message_count', header: 'Messages' },
    {
      id: 'interaction_details', header: 'Details',
      cell: ({ row }) => {
        const call = callsByConversation[row.original.id];
        if (!call) return <span className="text-xs text-foreground/50">WhatsApp chat</span>;
        const duration = call.duration_seconds == null ? '—' : `${Math.floor(call.duration_seconds / 60)}m ${call.duration_seconds % 60}s`;
        return <div className="flex flex-col gap-1"><span className="table-detail"><Clock/>{duration} · {call.status}</span><span className={`record-state ${call.recording_available ? 'available' : ''}`}><Mic/>{call.recording_available ? 'Recording available' : 'Not recorded'}</span></div>;
      },
    },
    { 
      accessorKey: 'updated_at', 
      header: 'Last Active',
      cell: ({ row }) => format(new Date(row.original.updated_at), 'dd/MM/yy')
    },
    {
      id: 'actions',
      header: 'View',
      cell: ({ row }) => (
        <button onClick={() => viewMessages(row.original)} className="icon-button" title="View conversation transcript" aria-label="View conversation transcript">
          <Eye className="w-4 h-4" />
        </button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="hero-panel !p-7">
        <div><div className="eyebrow"><span className="live-dot" /> Unified interaction history</div><h1 className="!text-3xl">Chats and calls</h1><p>Every WhatsApp chat, voice call, transcript and recording in one place.</p></div>
        <MessagesSquare className="relative z-10 hidden h-14 w-14 text-white/25 md:block" />
      </div>
      <div className="call-summary">
        <div><MessagesSquare/><span><strong>{summary.interactions}</strong>Total interactions</span></div>
        <div><MessageCircle/><span><strong>{summary.chats}</strong>WhatsApp chats</span></div>
        <div><PhoneCall/><span><strong>{summary.calls}</strong>Voice calls</span></div>
        <div><FileAudio/><span><strong>{summary.recordings}</strong>Recordings available</span></div>
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
        <TableSkeleton rows={10} cells={4} />
      ) : (
        <>
          <DataTable columns={columns} data={conversations} />
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

      <Modal isOpen={!!selectedConversation} onClose={() => setSelectedConversation(null)} title={`Conversation #${selectedConversation?.id}`}>
        {selectedConversation && <div className="conversation-audit-head">
          <div><span><Hash/>Conversation</span><strong>#{selectedConversation.id}</strong></div>
          <div><span>{selectedConversation.channel === 'call' ? <Phone/> : <MessageSquare/>}Channel</span><strong className="capitalize">{selectedConversation.channel}</strong></div>
          <div><span><Languages/>Language</span><strong>{selectedConversation.preferred_language || selectedConversation.detected_languages.join(', ') || 'Unknown'}</strong></div>
          <div><span><Tag/>Category IDs</span><strong>{selectedConversation.category_log_ids.map(id => `#${id}`).join(', ') || '—'}</strong></div>
          <div><span><Ticket/>Tracking IDs</span><strong>{selectedConversation.tracking_ids.join(', ') || '—'}</strong></div>
        </div>}
        {selectedConversation?.channel === 'call' && (() => {
          const call = callsByConversation[selectedConversation.id];
          if (!call) return null;
          return <div className="mx-4 mt-4 rounded-2xl border bg-foreground/[.025] p-4">
            <div className="mb-3 flex flex-wrap gap-4 text-xs"><span><strong>Call ID:</strong> CALL-{call.id}</span><span><strong>Status:</strong> {call.status}</span><span><strong>Provider:</strong> {call.provider_call_id}</span><span><strong>End reason:</strong> {call.end_reason || 'Normal'}</span></div>
            <div className="flex items-center gap-2 text-sm font-semibold"><FileAudio className="h-4 w-4 text-primary"/> Call recording</div>
            {recordingUrl ? <audio controls preload="metadata" src={recordingUrl} className="mt-2 w-full"/> : <p className="mt-2 text-xs text-foreground/50">This historical call was not recorded. New calls will show a player here.</p>}
          </div>;
        })()}
        <div className="flex-1 p-4 overflow-y-auto space-y-4 audit-transcript">
          {messagesLoading ? (
            <div>Loading messages...</div>
          ) : messages.length > 0 ? (
            messages.map(msg => (
              <div key={msg.id} className={msg.role}>
                <div>
                  <span>{msg.role === 'user' ? 'Passenger' : 'Metro AI'} · Message #{msg.id}</span>
                  <p>{msg.content}</p>
                  <time>{format(new Date(msg.created_at), 'dd/MM/yy, HH:mm')}</time>
                </div>
              </div>
            ))
          ) : (
            <div className="text-center text-foreground/60">No messages in this conversation.</div>
          )}
        </div>
      </Modal>
    </div>
  );
};

export default ConversationsPage;
