import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import { useSidebar } from '../../context/SidebarContext';

const DashboardLayout = () => {
  const { isCollapsed } = useSidebar();

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <div 
        className="flex min-w-0 flex-1 flex-col overflow-hidden transition-all duration-300"
        style={{ marginLeft: isCollapsed ? '72px' : '256px' }}
      >
        <TopBar />
        <main className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto bg-background p-4 md:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default DashboardLayout;
