export function Footer() {
  return (
    <footer 
      className="px-6 py-3 text-center text-sm border-t"
      style={{
        backgroundColor: 'var(--aigent-color-surface)',
        borderColor: 'var(--aigent-color-border)',
        color: 'var(--aigent-color-text-muted)'
      }}
    >
      Powered by <span style={{ color: 'var(--aigent-color-text)' }}>AIgentOS</span>
    </footer>
  );
}
