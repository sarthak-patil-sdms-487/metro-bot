import { useEffect, useState } from 'react';
import apiClient from '../services/apiClient';
import { DataTable } from '../components/ui/DataTable';
import { ColumnDef } from '@tanstack/react-table';
import { format } from 'date-fns';
import { User, PaginatedResponse } from '../types/api';
import { TableSkeleton } from '../components/ui/Skeleton';

const columns: ColumnDef<User>[] = [
  { accessorKey: 'id', header: 'ID' },
  { accessorKey: 'whatsapp_number', header: 'WhatsApp Number' },
  { accessorKey: 'name', header: 'Name' },
  { 
    accessorKey: 'created_at', 
    header: 'Joined At',
    cell: ({ row }) => format(new Date(row.original.created_at), 'dd/MM/yy')
  },
  { accessorKey: 'total_conversations', header: 'Conversations' },
];

const UsersPage = () => {
  const [data, setData] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [pageSize] = useState(20);

  useEffect(() => {
    const fetchUsers = async () => {
      setLoading(true);
      try {
        const response = await apiClient.get<PaginatedResponse<User>>(`/users?page=${page}&page_size=${pageSize}`);
        setData(response.data.items);
        setTotal(response.data.total);
      } catch (error) {
        console.error("Failed to fetch users", error);
      } finally {
        setLoading(false);
      }
    };
    fetchUsers();
  }, [page, pageSize]);

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Users</h1>
      {loading ? (
        <TableSkeleton rows={10} cells={5} />
      ) : (
        <>
          <DataTable columns={columns} data={data} />
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
    </div>
  );
};

export default UsersPage;
