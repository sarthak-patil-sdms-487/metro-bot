import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import { Sun, Moon, LogOut, Settings, HelpCircle, ChevronDown } from 'lucide-react';
import { useClickOutside } from '../../hooks/useClickOutside';

const TopBar = () => {
  const { logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  const dropdownRef = useClickOutside(() => {
    setIsDropdownOpen(false);
  });

  return (
    <header className="h-16 flex-shrink-0 bg-card/80 backdrop-blur-xl border-b border-border flex items-center justify-between px-6 z-30">
      <div className="hidden md:block"><p className="text-xs font-semibold uppercase tracking-[.16em] text-foreground/45">Operations</p><p className="text-sm font-medium">Pune Metro passenger support</p></div>
      <div className="flex items-center space-x-4">
        <button
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          className="p-2 rounded-full text-foreground/70 hover:bg-accent hover:text-accent-foreground transition-colors"
        >
          {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
        </button>

        <div className="relative" ref={dropdownRef}>
          <button 
            onClick={() => setIsDropdownOpen(prev => !prev)}
            className="flex items-center space-x-2 p-1 rounded-full hover:bg-accent transition-colors"
          >
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-primary-foreground font-semibold text-sm">
              A
            </div>
            <span className="text-sm font-medium hidden md:block">admin</span>
            <ChevronDown className={`w-4 h-4 text-foreground/70 transition-transform ${isDropdownOpen ? 'rotate-180' : ''}`} />
          </button>

          {isDropdownOpen && (
            <div className="absolute right-0 mt-2 w-64 bg-card border rounded-lg shadow-lg z-50">
              <div className="p-4 border-b">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-primary flex items-center justify-center text-primary-foreground font-semibold text-xl">
                    A
                  </div>
                  <div>
                    <p className="font-semibold">Administrator</p>
                    <p className="text-xs text-foreground/70">admin@punemetro.ai</p>
                  </div>
                </div>
              </div>
              <div className="p-2">
                <button className="w-full flex items-center gap-3 px-3 py-2 rounded-md hover:bg-accent text-sm">
                  <Settings className="w-4 h-4" />
                  Settings
                </button>
                <button className="w-full flex items-center gap-3 px-3 py-2 rounded-md hover:bg-accent text-sm" disabled>
                  <HelpCircle className="w-4 h-4" />
                  Help / Documentation
                </button>
              </div>
              <div className="p-2 border-t">
                <button 
                  onClick={logout}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-md hover:bg-danger/10 text-danger text-sm"
                >
                  <LogOut className="w-4 h-4" />
                  Logout
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default TopBar;
