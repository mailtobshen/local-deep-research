**PDF export now prints "第N页/共M页" page numbers in the page footer.**
The footer is centred, set in 五号 (9pt) 宋体, and rendered via
WeasyPrint's CSS Paged Media `@page @bottom-center` counter on the
server side and via a per-page html2canvas rasterisation on the
client side (jsPDF's built-in Helvetica cannot embed CJK glyphs).
