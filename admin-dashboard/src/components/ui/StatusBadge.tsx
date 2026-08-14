interface StatusBadgeProps {
  status: 'pending' | 'approved' | 'resolved' | 'rejected' | string;
}

const statusStyles: { [key: string]: { dot: string; text: string; bg: string; label: string } } = {
  pending: { dot: 'bg-warning', text: 'text-warning', bg: 'bg-warning/10', label: 'Pending' },
  approved: { dot: 'bg-info', text: 'text-info', bg: 'bg-info/10', label: 'Approved' },
  resolved: { dot: 'bg-success', text: 'text-success', bg: 'bg-success/10', label: 'Resolved' },
  rejected: { dot: 'bg-danger', text: 'text-danger', bg: 'bg-danger/10', label: 'Rejected' },
  answered: { dot: 'bg-blue-500', text: 'text-blue-600', bg: 'bg-blue-500/10', label: 'Answered' },
  recorded: { dot: 'bg-emerald-500', text: 'text-emerald-600', bg: 'bg-emerald-500/10', label: 'Recorded' },
  classified: { dot: 'bg-violet-500', text: 'text-violet-600', bg: 'bg-violet-500/10', label: 'Classified' },
  open: { dot: 'bg-violet-500', text: 'text-violet-600', bg: 'bg-violet-500/10', label: 'Classified' },
  active: { dot: 'bg-success', text: 'text-success', bg: 'bg-success/10', label: 'Active' },
  completed: { dot: 'bg-success', text: 'text-success', bg: 'bg-success/10', label: 'Completed' },
  failed: { dot: 'bg-danger', text: 'text-danger', bg: 'bg-danger/10', label: 'Failed' },
};

const StatusBadge = ({ status }: StatusBadgeProps) => {
  const styles = statusStyles[status] || {
    dot: 'bg-slate-400', text: 'text-slate-600', bg: 'bg-slate-500/10',
    label: status ? status.charAt(0).toUpperCase() + status.slice(1) : 'Unknown',
  };

  return (
    <div className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${styles.bg} ${styles.text}`}>
      <span className={`w-2 h-2 mr-2 rounded-full ${styles.dot}`}></span>
      {styles.label}
    </div>
  );
};

export default StatusBadge;
