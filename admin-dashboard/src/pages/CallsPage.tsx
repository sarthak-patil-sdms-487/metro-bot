import { useEffect, useState } from 'react';
import { format } from 'date-fns';
import { Clock, Eye, FileAudio, Languages, Mic, Phone, PhoneCall, Radio } from 'lucide-react';
import apiClient from '../services/apiClient';
import Modal from '../components/ui/Modal';
import StatusBadge from '../components/ui/StatusBadge';
import { CallSession, Message, PaginatedResponse } from '../types/api';
import { TableSkeleton } from '../components/ui/Skeleton';

const languageName: Record<string, string> = { english: 'English', hindi: 'Hindi', marathi: 'Marathi', 'en-IN': 'English', 'hi-IN': 'Hindi', 'mr-IN': 'Marathi' };
const duration = (seconds: number | null) => seconds == null ? '—' : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;

const CallsPage = () => {
  const [calls, setCalls] = useState<CallSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<CallSession | null>(null);
  const [transcript, setTranscript] = useState<Message[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 20;

  useEffect(() => {
    setLoading(true);
    apiClient.get<PaginatedResponse<CallSession>>(`/call-sessions?page=${page}&page_size=${pageSize}`)
      .then(r => { setCalls(r.data.items); setTotal(r.data.total); })
      .finally(() => setLoading(false));
  }, [page]);
  useEffect(() => () => { if (recordingUrl) URL.revokeObjectURL(recordingUrl); }, [recordingUrl]);

  const openCall = async (call: CallSession) => {
    setSelected(call); setDetailLoading(true); setTranscript([]); setRecordingUrl(null);
    try {
      const transcriptRequest = apiClient.get<Message[]>(`/conversations/${call.conversation_id}/messages`);
      const recordingRequest = call.recording_url ? apiClient.get<Blob>(call.recording_url, { responseType: 'blob' }) : Promise.resolve(null);
      const [messages, audio] = await Promise.all([transcriptRequest, recordingRequest]);
      setTranscript(messages.data);
      if (audio) setRecordingUrl(URL.createObjectURL(audio.data));
    } finally { setDetailLoading(false); }
  };

  const completed = calls.filter(call => call.status === 'completed').length;
  const recorded = calls.filter(call => call.recording_available).length;
  const languages = new Set(calls.flatMap(call => call.detected_languages)).size;

  return <div className="space-y-6">
    <div className="hero-panel !p-7"><div><div className="eyebrow"><span className="live-dot"/> Voice operations</div><h1 className="!text-3xl">WhatsApp call centre</h1><p>Call lifecycle, language detection, full transcripts and secured recordings.</p></div><PhoneCall className="relative z-10 hidden h-14 w-14 text-white/25 md:block"/></div>
    <div className="call-summary"><div><Phone/><span><strong>{total}</strong>Total calls</span></div><div><Radio/><span><strong>{completed}</strong>Completed on page</span></div><div><FileAudio/><span><strong>{recorded}</strong>Recordings on page</span></div><div><Languages/><span><strong>{languages}</strong>Languages on page</span></div></div>
    {loading ? <TableSkeleton rows={8} cells={7}/> : <div className="call-list panel !p-0">
      <div className="call-list-head"><span>Call / passenger</span><span>Language</span><span>Duration</span><span>Status</span><span>Recording</span><span>Date</span><span>View</span></div>
      {calls.map(call => <div className="call-row" key={call.id}>
        <div className="call-person"><i><PhoneCall/></i><span><strong>{call.user.name || call.user.whatsapp_number || 'Unknown passenger'}</strong><small>CALL-{call.id} · Conversation #{call.conversation_id}</small></span></div>
        <div className="language-tags">{call.detected_languages.length ? call.detected_languages.map(item => <span key={item}>{languageName[item] || item}</span>) : <span>Not detected</span>}</div>
        <div className="table-detail"><Clock/>{duration(call.duration_seconds)}</div>
        <StatusBadge status={call.status}/>
        <div className={`record-state ${call.recording_available ? 'available' : ''}`}><Mic/>{call.recording_available ? 'Available' : 'Not recorded'}</div>
        <span className="text-xs text-foreground/60">{format(new Date(call.started_at), 'dd/MM/yy')}</span>
        <button className="icon-button" onClick={() => openCall(call)} title="View call details" aria-label="View call details"><Eye/></button>
      </div>)}
      {!calls.length && <div className="empty-inline">No call sessions recorded yet.</div>}
    </div>}
    {total > pageSize && <div className="flex items-center justify-end gap-3 text-sm"><button className="secondary-button" disabled={page === 1} onClick={() => setPage(value => value - 1)}>Previous</button><span>Page {page} of {Math.ceil(total / pageSize)}</span><button className="secondary-button" disabled={page * pageSize >= total} onClick={() => setPage(value => value + 1)}>Next</button></div>}

    <Modal isOpen={!!selected} onClose={() => setSelected(null)} title={`Call #${selected?.id} audit`}>
      {selected && <div className="call-detail">
        <div className="detail-grid"><Info label="Passenger" value={selected.user.name || selected.user.whatsapp_number || 'Unknown'}/><Info label="WhatsApp number" value={selected.user.whatsapp_number || '—'}/><Info label="Provider call ID" value={selected.provider_call_id}/><Info label="Conversation ID" value={`#${selected.conversation_id}`}/><Info label="Started" value={format(new Date(selected.started_at), 'dd/MM/yy, HH:mm')}/><Info label="Duration" value={duration(selected.duration_seconds)}/><Info label="Direction" value={selected.direction}/><Info label="End reason" value={selected.end_reason || 'Normal / not supplied'}/></div>
        <section><h3><Languages/>Detected languages</h3><div className="language-tags">{selected.detected_languages.map(item => <span key={item}>{languageName[item] || item}</span>)}</div></section>
        <section><h3><FileAudio/>Call recording</h3>{recordingUrl ? <audio controls preload="metadata" src={recordingUrl} className="w-full"/> : <div className="recording-empty"><Mic/><div><strong>Recording unavailable</strong><p>Historical calls were not recorded. Recording is enabled for new calls after this update.</p></div></div>}</section>
        <section><h3><PhoneCall/>Full call transcript <small>{selected.transcript_count} messages</small></h3><div className="audit-transcript">{detailLoading ? 'Loading transcript…' : transcript.map(message => <div className={message.role} key={message.id}><span>{message.role === 'user' ? 'Passenger' : 'Metro AI'}</span><p>{message.content}</p><time>{format(new Date(message.created_at), 'dd/MM/yy, HH:mm')}</time></div>)}</div></section>
      </div>}
    </Modal>
  </div>;
};

const Info = ({ label, value }: { label: string; value: string }) => <div className="detail-info"><span>{label}</span><strong>{value}</strong></div>;
export default CallsPage;
