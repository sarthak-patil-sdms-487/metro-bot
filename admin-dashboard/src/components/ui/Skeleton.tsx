const Skeleton = ({ className }: { className?: string }) => {
  return (
    <div className={`animate-pulse rounded-md bg-accent ${className}`} />
  );
};

export const TableSkeleton = ({ rows = 5, cells = 5 }) => {
  return (
    <div className="rounded-lg border p-4">
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="grid grid-cols-5 gap-4">
            {Array.from({ length: cells }).map((_, j) => (
              <Skeleton key={j} className="h-6" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
};
