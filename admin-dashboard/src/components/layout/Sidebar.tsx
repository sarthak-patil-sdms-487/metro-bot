import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Users,
  MessageSquare,
  Ticket,
  Tags,
  PanelLeftClose,
  PanelLeftOpen,
  IndianRupee,
} from 'lucide-react';
import { useSidebar } from '../../context/SidebarContext';
import logo from '../../assets/pune-metro-logo.png';

const navItems = [
  { to: '/admin', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/admin/users', icon: Users, label: 'Users' },
  { to: '/admin/conversations', icon: MessageSquare, label: 'Interactions' },
  { to: '/admin/tickets', icon: Ticket, label: 'Service Tickets' },
  { to: '/admin/logs', icon: Tags, label: 'Classifications' },
  { to: '/admin/cost-audit', icon: IndianRupee, label: 'AI Cost & Cache' },
];

const Sidebar = () => {
  const { isCollapsed, toggleSidebar } = useSidebar();

  return (
    <aside 
      className={`app-sidebar fixed top-0 left-0 h-full text-white flex flex-col transition-all duration-300 z-50 ${isCollapsed ? 'w-[72px]' : 'w-64'}`}
    >
      <div className={`h-20 flex items-center border-b border-primary-foreground/10 relative ${isCollapsed ? 'justify-center' : 'justify-between px-4'}`}>
        <div className={`flex items-center gap-3 ${isCollapsed ? 'justify-center' : ''}`}>
          <div className={`flex h-10 w-10 items-center justify-center rounded-xl bg-white p-1.5 shadow-lg transition-all duration-300 ${isCollapsed ? 'h-9 w-9' : ''}`}>
            <img src={logo} alt="Pune Metro" className="h-full w-full object-contain" />
          </div>
          {!isCollapsed && <div><h1 className="text-base font-bold whitespace-nowrap">Pune Metro</h1><p className="text-[10px] uppercase tracking-[.22em] text-white/50">Passenger Care AI</p></div>}
        </div>
        {!isCollapsed && (
          <button 
            onClick={toggleSidebar} 
            className="p-1.5 rounded-md hover:bg-primary-foreground/5 transition-colors"
          >
            <PanelLeftClose className="w-6 h-6" />
          </button>
        )}
      </div>

      {isCollapsed && (
        <div className="py-2 flex items-center justify-center border-b border-primary-foreground/10">
          <button 
            onClick={toggleSidebar} 
            className="p-1.5 rounded-md hover:bg-primary-foreground/5 transition-colors"
          >
            <PanelLeftOpen className="w-6 h-6" />
          </button>
        </div>
      )}

      <nav className="flex-1 px-3 py-6 space-y-2">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/admin'}
            className={({ isActive }) =>
              `flex items-center p-3 rounded-md text-sm font-medium transition-colors relative group ${isCollapsed ? 'justify-center' : ''} ${
                isActive
                  ? 'bg-primary-foreground/10'
                  : 'hover:bg-primary-foreground/5'
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && <div className="absolute left-0 top-2 bottom-2 w-1 bg-secondary rounded-r-full"></div>}
                <item.icon className={`w-6 h-6 ${!isCollapsed && 'mr-4'}`} />
                {!isCollapsed && <span className="whitespace-nowrap">{item.label}</span>}
                {isCollapsed && (
                  <span className="absolute left-full ml-4 w-auto p-2 min-w-max rounded-md shadow-md text-white bg-gray-900 text-xs font-bold transition-all duration-100 scale-0 origin-left group-hover:scale-100 z-50">
                    {item.label}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
};

export default Sidebar;
