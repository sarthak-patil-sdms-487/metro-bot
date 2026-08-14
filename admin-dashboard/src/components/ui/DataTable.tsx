import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  ColumnDef,
} from '@tanstack/react-table';

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  fixed?: boolean;
}

export function DataTable<TData, TValue>({
  columns,
  data,
  fixed = false,
}: DataTableProps<TData, TValue>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="overflow-hidden rounded-lg border">
      <table className={`w-full text-sm ${fixed ? 'table-fixed' : 'table-auto'}`}>
        <thead className="border-b">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  style={fixed ? { width: header.getSize() } : undefined}
                  className="h-12 whitespace-nowrap px-3 text-left font-medium text-foreground/70 sticky top-0 bg-card"
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(
                        header.column.columnDef.header,
                        header.getContext()
                      )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows?.length ? (
            table.getRowModel().rows.map((row, index) => (
              <tr
                key={row.id}
                className={`
                  border-b transition-colors 
                  ${index % 2 === 0 ? 'bg-card' : 'bg-accent/50'}
                  hover:bg-accent
                `}
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    style={fixed ? { width: cell.column.getSize() } : undefined}
                    className="max-w-[260px] px-3 py-3.5 align-middle"
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={columns.length} className="h-24 text-center">
                No results.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
