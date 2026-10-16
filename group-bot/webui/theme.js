// Set theme before first paint; storage failure still leaves a usable light UI.
(()=>{
  let theme='light';
  try{if(localStorage.getItem('qq-debug-theme')==='dark')theme='dark';}catch{}
  document.documentElement.dataset.theme=theme;
  document.addEventListener('DOMContentLoaded',()=>{
    const button=document.getElementById('theme-toggle');
    function update(){const dark=document.documentElement.dataset.theme==='dark';button.textContent=dark?'白天':'黑夜';button.title=dark?'切换为白天模式':'切换为黑夜模式';button.setAttribute('aria-pressed',String(dark));}
    button.addEventListener('click',()=>{const next=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=next;try{localStorage.setItem('qq-debug-theme',next);}catch{}update();});
    update();
  });
})();
