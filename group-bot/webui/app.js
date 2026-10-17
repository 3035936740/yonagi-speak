let activePrompt='persona',activeScope='global',configSequence=0;const promptDrafts={},configDrafts={};

const $=id=>document.getElementById(id);
const keyFromURL=new URLSearchParams(location.hash.slice(1)).get('key');
if(keyFromURL){sessionStorage.setItem('qq-debug-key',keyFromURL);history.replaceState(null,'',location.pathname);}
const token=sessionStorage.getItem('qq-debug-key')||'';
let selected=null,kind='all',snapshot=null,configData=null,polling=false,pollSequence=0,lastEvents='',currentTab='logs',selectedEvent=null,closed=false,toastTimer=0;
const labels={system:'系统干预',received:'收到消息',sent:'Bot 发送',stage:'执行阶段',decision:'决策',trigger:'触发',group:'会话'};
const titles={bot_name:'人格名字',bot_aliases:'点名识别别名',model:'回复模型',reasoning_effort:'思考等级',fast_mode:'Fast 模式',passive_probability:'普通水群概率',sticker_reply_probability:'表情反应偏好',no_words_sticker_probability:'纯表情偏好',followup_sticker_probability:'文字后跟表情偏好',burst_reply_probability:'连续分段发送偏好',friendly_tease_probability:'朋友间玩笑偏好',console_max_text_chars:'调试记录文本上限',console_max_records:'调试记录条数上限',max_prompt_chars:'普通模型输入字符上限',context_messages:'传入模型的近期消息条数',max_replies_per_hour:'每小时消息上限',max_images_per_hour:'每小时绘图上限',image_summary_window_messages:'图片摘要滑动有效期',image_repeat_window_messages:'复读观察窗口',image_repeat_min_count:'复读候选次数',image_repeat_pair_confidence:'连续两次的复读置信度',image_repeat_full_streak:'明确复读所需连续次数',chat_group_whitelist:'群白名单',enabled:'机器人启用',multi_role_enabled:'模型分工',role_models:'分工角色模型',shared_sticker_library:'跨群共用表情库',style_learning_enabled:'表达学习',memory_enabled:'群聊记忆',proactive_topics_enabled:'主动开话题',image_generation_enabled:'绘图启用',recent_chat_journal_max_messages:'近期聊天存档条数',recent_chat_journal_hours:'近期聊天存档时长'};
// First matching category wins; anything unmatched lands in "其他".
const categories=[
  ['identity','身份与开关','Bot 叫什么、是否启用、在哪些群工作',key=>['enabled','bot_name','bot_aliases','chat_group_whitelist'].includes(key)],
  ['model','模型','生成回复用的模型、思考强度和分工角色',key=>/^(model|reasoning_effort|fast_mode|multi_role_enabled|role_models|role_timeout_seconds|model_timeout_seconds|text_transport)$/.test(key)],
  ['member-reference','群友资料：模型参考','选择允许传给模型参考的账号资料，默认全部开启',key=>/^member_reference_/.test(key)],
  ['member-prompts','群友资料：类型提示词','按当前发言者的账号类型使用，支持全局及群覆盖',key=>/^member_prompt_/.test(key)],
  ['member-block','群友资料：屏蔽规则','默认关闭自动屏蔽；人工解除优先，后台仍保留消息',key=>/^member_model_block_/.test(key)],
  ['member-model','群友资料：模型自行标记','独立控制模型修改三项资料的权限，默认全部关闭；人工资料优先',key=>/^member_model_/.test(key)],
  ['member-rules','群友资料：规则判断','按指令响应行为推断bot，不调用模型',key=>/^member_auto_/.test(key)],
  ['participation','参与群聊的节奏','多久说一次话、什么时候接话、怎么回复',key=>/^passive_|^proactive_|^(adaptive_participation_enabled|social_planner_enabled|conversation_state_ttl_seconds|mention_cooldown_seconds|poll_seconds|peer_reply_enabled|at_reply_probability|quote_reply_probability|burst_reply_probability|friendly_tease_probability)$/.test(key)],
  ['sticker','表情','发表情的偏好与共享表情库的收录规则',key=>/sticker/.test(key)],
  ['cleanup','生成图片的清理','Bot 生成的图片保留多久、怎么清理',key=>/^generated_image_/.test(key)],
  ['image','识图与绘图','读取群友图片、复读识别和绘图',key=>/^image_|^vision_|^max_images?_|background_image_reading_enabled/.test(key)],
  ['memory','记忆与学习','群聊记忆、表达学习和近期聊天存档',key=>/memory|learning|^style_|^semantic_|^recent_chat_journal/.test(key)],
  ['limits','额度与上下文','每小时/每天的上限，以及传给模型的上下文长度',key=>/^max_|^context_/.test(key)],
  ['debug','界面与容错','WebUI监听地址、聊天显示、记录容量和失败处理',key=>/^webui_|^console_|^reply_failure|^chat_image_display$/.test(key)],
  ['other','其他','',()=>true]];
const promptInfo={persona:'Bot 的性格、说话方式和背景设定。保存后下一轮回复生效。',reply_protocol:'Bot 回复时要遵守的格式和行为规则。改错可能导致回复失败，保存前会自动备份旧版本。'};
const shanghai={timeZone:'Asia/Shanghai',hour12:false};
function node(tag,text,className){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;}
function toast(text,error=false){const el=$('toast');el.textContent=text;el.classList.toggle('error',error);el.hidden=!text;clearTimeout(toastTimer);if(text)toastTimer=setTimeout(()=>{el.hidden=true;},error?7000:3500);}
function initial(text){return String(text||'?').trim().charAt(0).toUpperCase()||'?';}
const avatarCache=new Map();
function avatarNode(kind,id,name,className,group=selected){
  const el=node('span',initial(name),className);if(!/^\d+$/.test(String(id||'')))return el;
  const cacheKey=group+':'+kind+':'+id;
  let entry=avatarCache.get(cacheKey);
  if(!entry||entry.failed&&Date.now()-entry.time>60000){
    entry={time:Date.now(),failed:false,url:null};
    entry.promise=fetch('/api/avatar?'+new URLSearchParams({group,kind,id}),{headers:{'X-QQ-Debug-Key':token}})
      .then(response=>{if(!response.ok)throw Error('avatar unavailable');return response.blob();})
      .then(blob=>{entry.url=URL.createObjectURL(blob);return entry.url;}).catch(()=>{entry.failed=true;return null;});
    avatarCache.set(cacheKey,entry);
    while(avatarCache.size>400){const [key,value]=avatarCache.entries().next().value;avatarCache.delete(key);value.promise.then(url=>{if(url)URL.revokeObjectURL(url);});}
  }
  entry.promise.then(url=>{if(!url)return;const img=node('img',undefined,'avatar-image');img.alt='';img.decoding='async';img.onload=()=>el.append(img);img.onerror=()=>img.remove();img.src=url;});
  return el;
}
window.addEventListener('pagehide',()=>{for(const entry of avatarCache.values())if(entry.url)URL.revokeObjectURL(entry.url);avatarCache.clear();});
const interventionModes={context:'当前对话窗口',once:'下一次自然回复',timed:'定时失效',persistent:'直到手动清除'};
const interventionStates={active:'生效中',expired:'已失效',cleared:'已清除',consumed:'已使用'};
function interventionDelete(entry,group=selected){const button=node('button','删除','danger-text system-note-delete');button.type='button';button.disabled=!snapshot?.features?.includes('intervention_delete');button.title=button.disabled?'请重载Debug后台以启用删除':'删除后停止影响后续生成';button.onclick=async event=>{event.stopPropagation();try{await act({action:'system',mode:'delete',id:entry.id,group},button);if($('intervention-dialog').open)await loadInterventions();}catch{}};return button;}
function systemRow(row){const item=node('div',undefined,'system-note'),head=node('div',undefined,'system-note-header'),entry=row.intervention||{};head.append(node('strong','系统干预'),node('span',interventionStates[entry.status]||entry.status||''),node('span',interventionModes[entry.mode]||''),node('span',eventTime(row)),interventionDelete(entry));item.append(head,node('div',row.text||'','system-note-text'));return item;}
const composerDrafts={},manualPending=new Map(),manualDraftIDs=new Map(),composerResults=new Map(),composerBusy=new Set();
const composerAttachments=new Map(),composerImageLoading=new Set();let composerUploadTarget=null;
function composerKey(){return $('composer-role').value+':'+selected;}
function attachmentsFor(key){return composerAttachments.get(key)||[];}
function attachmentSignature(key){return attachmentsFor(key).map(image=>image.id).join(':');}
function currentDraftText(key){return $('composer-text').dataset.key===key?$('composer-text').value:composerDrafts[key]?.text||'';}
function clearAttachments(key){for(const image of attachmentsFor(key))URL.revokeObjectURL(image.url);composerAttachments.delete(key);}
function composerResult(group,role,text,error=false){composerResults.set(role+':'+group,{text,error});if(group===selected)renderComposer();}
function renderAttachments(key,system,locked){
  const area=$('composer-attachments'),images=attachmentsFor(key);area.hidden=system||!images.length;
  const signature=key+':'+attachmentSignature(key)+':'+locked;
  if(area.dataset.signature===signature)return;area.dataset.signature=signature;area.replaceChildren();
  for(const image of images){
    const card=node('div',undefined,'composer-attachment'),preview=node('button',undefined,'composer-image-preview'),img=node('img');
    img.src=image.url;img.alt='图片'+image.slot+'：'+image.file.name;preview.type='button';preview.title='查看图片'+image.slot;preview.append(img);
    preview.onclick=()=>showOriginal(image.url,image.file.name||'待发送图片');
    const caption=node('span','[图片'+image.slot+']','code'),remove=node('button','×','composer-image-remove');
    remove.type='button';remove.disabled=locked;remove.setAttribute('aria-label','移除图片'+image.slot);
    remove.onclick=()=>{
      if(composerBusy.has(key)||composerImageLoading.has(key)||manualPending.has(Number(key.split(':')[1])))return;
      composerAttachments.set(key,attachmentsFor(key).filter(item=>item.id!==image.id));URL.revokeObjectURL(image.url);
      const text=currentDraftText(key).replaceAll('[图片'+image.slot+']','');composerDrafts[key]={...composerDrafts[key],text};
      if($('composer-text').dataset.key===key)$('composer-text').value=text;renderComposer();
    };
    card.append(preview,caption,remove);area.append(card);
  }
}
function renderComposer(){
  if(!snapshot)return;const input=$('composer-text'),role=$('composer-role').value,key=composerKey(),previous=input.dataset.key;
  if(previous!==key){if(previous)composerDrafts[previous]={...composerDrafts[previous],text:input.value,scope:$('composer-scope').value,lifetime:$('composer-lifetime').value,minutes:$('composer-minutes').value};const draft=composerDrafts[key]||{};input.value=draft.text||'';$('composer-scope').value=draft.scope||'group';$('composer-lifetime').value=draft.lifetime||'context';$('composer-minutes').value=draft.minutes||'30';input.dataset.key=key;}
  const system=role==='system',s=snapshot.status,images=attachmentsFor(key),locked=composerBusy.has(key)||composerImageLoading.has(key)||!system&&manualPending.has(selected);
  const ready=system?snapshot.features?.includes('system_interventions'):snapshot.features?.includes('manual_send')&&s.enabled&&s.online&&s.manual_send_ready;
  const imagesSupported=snapshot.features?.includes('manual_images');
  document.querySelectorAll('[data-system-option]').forEach(el=>el.hidden=!system);$('composer-minutes-row').hidden=!system||$('composer-lifetime').value!=='timed';
  input.maxLength=system?1500:2000;input.disabled=Boolean(locked);
  input.placeholder=(system?'输入系统提示词，不会立即触发回复':'以 '+s.name+' 的身份向当前群发送消息，可上传或粘贴图片，按 [图片1] 的位置发送')+'\nEnter 发送 · Shift+Enter 换行';
  $('composer-upload').hidden=system;$('composer-upload').disabled=Boolean(locked||!imagesSupported||images.length>=2);
  $('composer-upload').title=imagesSupported?'最多2张；单张≤5MB，合计≤8MB；支持PNG/JPEG/GIF/WebP':'请 /stop 后重新启动后台以加载图片上传';
  renderAttachments(key,system,Boolean(locked));
  $('composer-role').options[1].textContent=s.name+' 消息（发到群）';$('composer-send').textContent=system?'提交系统提示词':'发送 '+s.name+' 消息';
  $('composer-send').disabled=!ready||(!input.value.trim()&&!images.length)||Boolean(locked)||(!system&&images.length>0&&!s.manual_images_ready);
  $('composer-hint').textContent=system?'不立即回复；当前对话窗口 '+s.text_limit+' 字符。Enter 提交，Shift+Enter 换行':!ready?'先启动并加载支持手动发送的群后台':images.length&&!s.manual_images_ready?'图片发送需要新版群后台，请 /stop 后手动重启':'发送到本群 '+selected+'，文字和图片同条发送并加入记忆。最多2张，单张≤5MB，合计≤8MB。Enter 发送，Shift+Enter 换行';
  const result=composerResults.get(key);$('composer-result').textContent=result?.text||'';$('composer-result').classList.toggle('error',Boolean(result?.error));
}
async function addComposerImages(files,target,plain=''){
  const key=target.key,group=Number(key.split(':')[1]);
  if(!key.startsWith('bot:'))throw Error('图片仅支持“消息（发到群）”，系统提示词不能添加图片');
  if(!snapshot?.features?.includes('manual_images'))throw Error('请 /stop 后重新启动后台以加载图片上传');
  if(composerBusy.has(key)||composerImageLoading.has(key)||manualPending.has(group))throw Error('当前消息正在发送，请等待结果');
  const previous=attachmentsFor(key),limits=snapshot.status.manual_image_limits||{max_images:2,max_image_bytes:5*1024*1024,max_total_bytes:8*1024*1024};
  if(previous.length+files.length>limits.max_images)throw Error('最多附加2张图片');
  let total=previous.reduce((sum,item)=>sum+item.file.size,0);
  for(const file of files){if(!file.size||file.size>limits.max_image_bytes)throw Error('单张图片上限5MB');if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)&&!(file.type===''&&/\.(png|jpe?g|gif|webp)$/i.test(file.name)))throw Error('只支持PNG、JPEG、GIF、WebP图片');total+=file.size;}
  if(total>limits.max_total_bytes)throw Error('图片附件合计上限8MB');
  composerImageLoading.add(key);renderComposer();
  try{
  let frozen;
  try{frozen=await Promise.all(files.map(async file=>new File([await file.arrayBuffer()],file.name,{type:file.type,lastModified:file.lastModified})));}
  catch{throw Error('图片读取失败，请重新上传或粘贴');}
  let slot=Math.max(0,...previous.map(image=>image.slot));const added=frozen.map(file=>({id:crypto.randomUUID(),slot:++slot,file,url:URL.createObjectURL(file)}));
  if(slot>999){for(const image of added)URL.revokeObjectURL(image.url);throw Error('图片编号过多，请清空附件后重试');}
  const text=currentDraftText(key),at=Math.min(target.start??text.length,text.length),end=Math.min(target.end??at,text.length);
  const inserted=plain+added.map(image=>'[图片'+image.slot+']').join('');
  if(text.length-(end-at)+inserted.length>2000){for(const image of added)URL.revokeObjectURL(image.url);throw Error('文字和图片占位符合计最多2000字');}
  composerAttachments.set(key,[...previous,...added]);const next=text.slice(0,at)+inserted+text.slice(end);composerDrafts[key]={...composerDrafts[key],text:next};
  if($('composer-text').dataset.key===key){$('composer-text').value=next;$('composer-text').focus();$('composer-text').setSelectionRange(at+inserted.length,at+inserted.length);}
  renderComposer();
  }finally{composerImageLoading.delete(key);renderComposer();}
}
function imagePayload(image){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(Error('图片读取失败'));reader.onload=()=>resolve({slot:image.slot,data:String(reader.result).split(',')[1]});reader.readAsDataURL(image.file);});}
async function checkManualSend(group){
  const pending=manualPending.get(group);if(!pending||pending.checking)return;pending.checking=true;
  try{
    const row=await api('/api/manual-send?'+new URLSearchParams({group,id:pending.id}));
    if(['queued','sending','delivered','record_pending'].includes(row.status)){composerResult(group,'bot',row.status==='record_pending'?'QQ已发送，本地记录正在补写':'手动消息正在排队或发送…');}
    else{
      manualPending.delete(group);manualDraftIDs.delete('bot:'+group);
      if(row.status==='sent'){
        const key='bot:'+group;
        if(currentDraftText(key)===pending.text&&attachmentSignature(key)===pending.attachments){
          clearAttachments(key);composerDrafts[key]={text:''};if($('composer-text').dataset.key===key)$('composer-text').value='';
        }
        composerResult(group,'bot','已发送，消息 ID '+row.message_id+'；图文已加入上下文与手动消息记忆');
      }else composerResult(group,'bot',row.error||'未发送，请检查后台',true);
    }
  }catch(error){composerResult(group,'bot','暂时无法确认发送结果，请核对QQ：'+error.message,true);}
  finally{pending.checking=false;}
}
$('composer-role').onchange=()=>renderComposer();$('composer-lifetime').onchange=()=>renderComposer();$('composer-text').oninput=()=>renderComposer();
$('composer-upload').onclick=()=>{const input=$('composer-text');composerUploadTarget={key:composerKey(),start:input.selectionStart,end:input.selectionEnd};$('composer-files').click();};
$('composer-files').onchange=async()=>{try{const files=[...$('composer-files').files];if(files.length)await addComposerImages(files,composerUploadTarget||{key:composerKey()});}catch(error){composerResult(Number((composerUploadTarget?.key||composerKey()).split(':')[1]),'bot',error.message,true);}finally{$('composer-files').value='';composerUploadTarget=null;}};
$('composer-text').addEventListener('paste',async event=>{
  const files=[...(event.clipboardData?.items||[])].filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);if(!files.length)return;
  event.preventDefault();try{await addComposerImages(files,{key:composerKey(),start:event.target.selectionStart,end:event.target.selectionEnd},event.clipboardData.getData('text/plain')||'');}catch(error){composerResult(selected,$('composer-role').value,error.message,true);}
});
$('composer-text').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.altKey&&!event.metaKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();if(!$('composer-send').disabled)$('composer').requestSubmit();}};
$('composer').onsubmit=async event=>{
  event.preventDefault();const group=selected,role=$('composer-role').value,text=$('composer-text').value,key=composerKey(),images=attachmentsFor(key),attachmentIDs=attachmentSignature(key);
  if((!text.trim()&&!images.length)||composerBusy.has(key)||composerImageLoading.has(key)||role==='bot'&&manualPending.has(group))return;
  composerBusy.add(key);renderComposer();
  try{
    if(role==='system'){
      await act({action:'system',mode:'add',group,text,target_scope:$('composer-scope').value,lifetime:$('composer-lifetime').value,minutes:Number($('composer-minutes').value)},$('composer-send'));
      composerDrafts[key]={text:''};if($('composer-text').dataset.key===key&&$('composer-text').value===text)$('composer-text').value='';composerResult(group,role,'系统提示词已保存，不触发立即回复');
    }else{
      const fingerprint=JSON.stringify([text,attachmentIDs]);let request=manualDraftIDs.get(key);
      if(!request||request.fingerprint!==fingerprint){request={id:crypto.randomUUID().replaceAll('-',''),fingerprint};manualDraftIDs.set(key,request);}
      const payload=await Promise.all(images.map(imagePayload));
      const data=await act({action:'manual_send',group,text,images:payload,request_id:request.id},$('composer-send'));
      manualPending.set(group,{id:data.request.id,text,attachments:attachmentIDs});composerResult(group,role,'手动图文消息已排队，等待群后台发送');await checkManualSend(group);
    }
  }catch(error){composerResult(group,role,error.message,true);}finally{composerBusy.delete(key);renderComposer();}
};
async function api(path,body){const response=await fetch(path,{headers:{'X-QQ-Debug-Key':token,...(body?{'Content-Type':'application/json'}:{})},method:body?'POST':'GET',...(body?{body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
async function act(action,button){if(backendReady!==true&&!['command','restart','pause','delete'].includes(action.action))throw Error('调试服务还是旧版本：请在 CLI 输入 /exit 后重新启动 Debug');const operationGroup=action.group??selected;if(button)button.disabled=true;toast('正在处理…');try{const data=await api('/api/action',{group:operationGroup,...action});if(data.selected!==undefined&&selected===operationGroup)selected=data.selected;toast(data.message||'已完成');if(data.shutdown){closed=true;pollSequence++;$('connection').textContent='调试服务已退出';setDot('connection-dot',false);document.querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=true);}else await poll(true);return data;}catch(error){toast(error.message,true);throw error;}finally{if(button)button.disabled=closed;}}
function setDot(id,online,error=false){$(id).classList.toggle('live',online);$(id).classList.toggle('error',error);}

const groupViews=new Map(),emptyGroups=node('div','','empty');
function renderGroups(){
  const term=$('group-search').value.toLowerCase(),container=$('groups'),present=new Set(snapshot.groups.map(group=>String(group.id))),visible=[];
  $('group-count').textContent=snapshot.groups.length;
  for(const [id,view] of groupViews)if(!present.has(id)){view.button.remove();groupViews.delete(id);}
  for(const group of snapshot.groups){
    const id=String(group.id);let view=groupViews.get(id);
    if(!view){
      const button=node('button',undefined,'group'),avatar=avatarNode('group',group.id,group.name,'group-avatar',group.id),dot=node('span',undefined,'dot'),text=node('span'),name=node('span','','group-name'),sub=node('span','','group-sub');
      button.type='button';button.dataset.group=id;avatar.append(dot);text.append(name,sub);button.append(avatar,text);
      button.onclick=()=>{selectGroup(group.id);if(['system','stickers'].includes(currentTab))switchTab('logs');};
      view={button,avatar,dot,name,sub};groupViews.set(id,view);
    }
    const {button,dot,name,sub}=view,title=group.name+'（'+group.id+'）',subtitle=groupState(group,group)[0]+' · '+group.id,active=String(selected)===id;
    button.classList.toggle('active',active);button.setAttribute('aria-current',String(active));button.title=title;
    dot.classList.toggle('live',group.enabled!==false&&group.online&&!group.stopped);
    if(name.textContent!==group.name)name.textContent=group.name;
    if(sub.textContent!==subtitle)sub.textContent=subtitle;
    view.avatar.firstChild.nodeValue=initial(group.name);
    const cached=avatarCache.get(group.id+':group:'+group.id);
    if(cached?.failed&&Date.now()-cached.time>60000&&!view.avatar.querySelector('img')){
      const replacement=avatarNode('group',group.id,group.name,'group-avatar',group.id);replacement.append(dot);view.avatar.replaceWith(replacement);view.avatar=replacement;
    }
    if((group.name+' '+id).toLowerCase().includes(term))visible.push(button);
  }
  if(!visible.length){emptyGroups.textContent=snapshot.groups.length?'没有匹配的群':'还没有授权的群，在下面输入群号添加';visible.push(emptyGroups);}
  for(const child of [...container.children])if(!visible.includes(child))child.remove();
  visible.forEach((child,index)=>{if(container.children[index]!==child)container.insertBefore(child,container.children[index]||null);});
}
function selectGroup(group){stashPromptDraft();selected=group;selectedEvent=null;lastEvents='';configData=null;databaseData=null;dbSequence++;$('db-add').disabled=true;$('settings').replaceChildren(node('div','正在读取这个群的设置…','empty'));poll(true).then(()=>loadActivePane()).catch(error=>toast(error.message,true));}

let memberSequence=0,memberEditor=null,memberEditorSequence=0;
const memberLabelText=value=>({AI:'AI',bot:'bot',human:'真人',owner:'bot主'}[value]||'未标记');
const memberSourceText=value=>({manual:'手动',model:'模型推断',auto:'规则推断'}[value]||'未标记');
function memberEvidence(member){return (member.blocked?'已屏蔽（'+memberSourceText(member.block_source)+'），不进入模型。':'')+'当前：'+memberLabelText(member.label)+'（'+memberSourceText(member.source)+'）；独立指令响应 '+(member.observations||0)+' 次。'+(Object.keys(member.prefix_counts||{}).length?'各前缀：'+Object.entries(member.prefix_counts).map(([prefix,count])=>prefix+' '+count+'次').join('、')+'。':'')+(member.evidence||[]).map(item=>item.command+' → 消息 '+item.response_id).join('；')+(member.model_history?.length?' 模型资料修改：'+member.model_history.map(item=>({account_type:'账号类型',extra_names:'额外昵称',account_note:'账号说明'}[item.field]||item.field)+' · 证据 '+item.evidence_message_ids.join('、')).join('；'):'');}
async function loadMembers(){
  const ticket=++memberSequence,group=selected,data=await api('/api/members?'+new URLSearchParams({group,q:$('member-search').value}));
  if(ticket!==memberSequence||group!==selected)return;
  $('member-count').textContent='共 '+data.total+' 位群友，匹配 '+data.matched+' 位'+(data.matched>data.limit?'；最多显示 '+data.limit+' 位，请缩小搜索范围':'');
  const table=node('table'),head=node('tr');for(const title of ['群友','额外昵称','标记','证据 / 说明','操作'])head.append(node('th',title));table.append(head);
  for(const member of data.members){
    const row=node('tr'),person=node('td'),names=node('td',member.aliases?.join('、')||'—'),type=node('td',(member.blocked?'屏蔽 · ':'')+memberLabelText(member.label)+' · '+memberSourceText(member.source)),evidence=node('td',(member.observations?'指令响应 '+member.observations+' 次。':'')+(member.note||'')),actions=node('td'),open=node('button','编辑资料');
    const name=member.card||member.nickname||member.qq_id,avatar=avatarNode('user',member.qq_id,name,'group-avatar',group);avatar.classList.add('member-avatar-button');avatar.setAttribute('role','button');avatar.tabIndex=0;avatar.setAttribute('aria-label','查看并标记群友 '+name);avatar.onclick=()=>openMember(member.qq_id,name);avatar.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();avatar.click();}};
    const cell=node('div',undefined,'member-person');cell.append(avatar,node('span',name+' · '+member.qq_id));person.append(cell);open.type='button';open.onclick=()=>openMember(member.qq_id,name).catch(error=>toast(error.message,true));actions.append(open);row.append(person,names,type,evidence,actions);table.append(row);
  }
  $('member-records').replaceChildren(data.members.length?table:node('div','没有匹配的群友，可在上面填写QQ号手动标记','empty'));
}
async function openMember(account,name){
  const ticket=++memberEditorSequence,group=selected;
  const data=await api('/api/members?'+new URLSearchParams({group,q:account}));
  if(ticket!==memberEditorSequence||group!==selected)return;
  const member=data.members.find(item=>String(item.qq_id)===String(account))||{qq_id:String(account),nickname:name},person=$('member-dialog-person');memberEditor={group,account:String(account),blocked:Boolean(member.blocked),blockDirty:false};
  person.replaceChildren(avatarNode('user',account,name,'group-avatar',group),node('strong',member.card||member.nickname||name),node('span',String(account),'code'));
  $('member-dialog-label').value=member.manual_label||'auto';$('member-dialog-aliases').value=(member.aliases||[]).join(', ');$('member-dialog-note').value=member.note||'';
  $('member-dialog-blocked').checked=Boolean(member.blocked);$('member-dialog-blocked').onchange=()=>{if(memberEditor)memberEditor.blockDirty=true;};
  $('member-dialog-evidence').textContent=memberEvidence(member);
  $('member-dialog-result').textContent='';if(!$('member-dialog').open)$('member-dialog').showModal();
}
$('member-dialog-close').onclick=()=>{$('member-dialog').close();memberEditor=null;};
$('member-dialog-form').onsubmit=async event=>{event.preventDefault();if(!memberEditor)return;const editor={...memberEditor},button=$('member-dialog-save');button.disabled=true;try{
  const aliases=$('member-dialog-aliases').value.split(/[,，\n]/).map(name=>name.trim()).filter(Boolean);
  const saved=await act({action:'member_label',group:editor.group,account:editor.account,label:$('member-dialog-label').value,aliases,note:$('member-dialog-note').value,...(editor.blockDirty?{blocked:$('member-dialog-blocked').checked}:{})},button);
  $('member-dialog-blocked').checked=Boolean(saved.member.blocked);if(memberEditor)memberEditor.blockDirty=false;
  $('member-dialog-evidence').textContent=memberEvidence(saved.member);
  $('member-dialog-label').value=saved.member.manual_label||'auto';$('member-dialog-aliases').value=(saved.member.aliases||[]).join(', ');$('member-dialog-note').value=saved.member.note||'';
  $('member-dialog-result').textContent='已保存，后续处理按新资料与屏蔽设置生效';if(currentTab==='members'&&selected===editor.group)await loadMembers();
}catch(error){$('member-dialog-result').textContent=error.message;}finally{button.disabled=false;}};
$('member-form').onsubmit=async event=>{event.preventDefault();const group=selected;try{await act({action:'member_label',group,account:$('member-account').value.trim(),label:$('member-label').value,note:$('member-note').value},$('member-save'));if(group===selected)await loadMembers();}catch{}};
$('member-refresh').onclick=()=>loadMembers().catch(error=>toast(error.message,true));
$('member-search').oninput=()=>loadMembers().catch(error=>toast(error.message,true));

const eventKey=row=>JSON.stringify([row.time,row.kind,row.message_id,row.role,['received','sent'].includes(row.kind)?'':row.text]);
const contextID=row=>String(row.context_id||row.trace_id||row.request_id||row.message_id||'—');
const validTime=row=>Number.isFinite(Number(row.time))&&Number(row.time)>0;
function eventTime(row){return validTime(row)?new Intl.DateTimeFormat('zh-CN',{...shanghai,hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(row.time*1000)):'—';}
function eventDate(row){return validTime(row)?new Intl.DateTimeFormat('zh-CN',{...shanghai,month:'long',day:'numeric',weekday:'short'}).format(new Date(row.time*1000)):'';}
function eventSummary(row){let text=row.text||'';if(row.kind==='decision'){try{const obj=JSON.parse(text);text=Object.entries(obj).map(([key,value])=>key+'：'+(typeof value==='object'?JSON.stringify(value):value)).join('，');}catch{}}return text.replace(/\s+/g,' ').trim();}
function levelClass(row){return ['ERROR','CRITICAL'].includes(row.level)?'bad':row.level==='WARNING'?'warn':row.kind;}
const recordViews=new Map();let recordViewScope='';
function recordSignature(row){return JSON.stringify(['received','sent'].includes(row.kind)?
  [row.text,row.sender,row.user_id,row.quote,row.blocked,row.manual,row.historical,row.level,(row.image_info||[]).map(image=>image.key),row.kind==='sent'?snapshot.status.name:'']:[row,snapshot.features]);}
function renderRecords(){
  const level=$('level-filter').value,rows=snapshot.events.filter(row=>level==='all'||(level==='WARNING'?['WARNING','ERROR','CRITICAL'].includes(row.level):['ERROR','CRITICAL'].includes(row.level)));
  const scope=selected+':'+(snapshot.status.chat_image_display||'image'),fingerprint=scope+level+snapshot.status.name+JSON.stringify(rows);
  if(lastEvents===fingerprint)return;lastEvents=fingerprint;
  const container=$('records'),scroll=container.scrollTop,atBottom=container.scrollHeight-scroll-container.clientHeight<80;
  if(recordViewScope!==scope){releaseChatImageLoads(container);recordViews.clear();container.replaceChildren();recordViewScope=scope;}
  if(!rows.some(row=>eventKey(row)===selectedEvent))selectedEvent=null;
  const desired=[],liveKeys=new Set();let day='';
  const reuse=(key,signature,create)=>{liveKeys.add(key);let entry=recordViews.get(key);if(!entry||entry.signature!==signature){if(entry)releaseChatImageLoads(entry.item);entry={signature,item:create()};recordViews.set(key,entry);}desired.push(entry.item);return entry.item;};
  for(const row of rows){
    const date=eventDate(row);if(date&&date!==day){day=date;reuse('day:'+date,date,()=>node('div',date,'day-mark'));}
    const chat=['received','sent'].includes(row.kind),key=eventKey(row);
    const item=reuse('row:'+key,recordSignature(row),()=>chat?messageRow(row):row.kind==='system'?systemRow(row):flowRow(row));
    item.updateRecord?.(row);item.title=eventSummary(row);item.classList.toggle('selected',key===selectedEvent);item.tabIndex=0;item.setAttribute('role','option');item.setAttribute('aria-selected',String(key===selectedEvent));item.dataset.level=row.level||'INFO';
    const choose=()=>{selectedEvent=key;container.querySelectorAll('[role=option]').forEach(other=>{const active=other===item;other.classList.toggle('selected',active);other.setAttribute('aria-selected',String(active));});renderDetail(row);};
    item.onclick=choose;item.onkeydown=event=>{if(event.target===item&&(event.key==='Enter'||event.key===' ')){event.preventDefault();choose();}};
  }
  if(!rows.length)reuse('empty',[$('record-search').value,level,kind].join(':'),()=>node('div',$('record-search').value||level!=='all'||kind!=='all'?'没有符合筛选条件的记录':'这个群还没有记录。群里有新消息后会出现在这里','empty'));
  for(const [key,entry] of recordViews){if(!liveKeys.has(key)){releaseChatImageLoads(entry.item);entry.item.remove();recordViews.delete(key);}}
  // Leave unchanged nodes connected: replacing the list restarts image/GIF loads.
  const keep=new Set(desired);for(const child of [...container.children])if(!keep.has(child)){releaseChatImageLoads(child);child.remove();}
  let cursor=container.firstChild;for(const item of desired){if(item===cursor)cursor=cursor.nextSibling;else container.insertBefore(item,cursor);}
  if($('follow').checked&&atBottom)container.scrollTop=container.scrollHeight;else container.scrollTop=scroll;
  renderDetail(rows.find(row=>eventKey(row)===selectedEvent));$('trace-count').textContent=rows.length;
  const chars=kind==='flow'?(snapshot.status.flow_text_chars??snapshot.status.text_chars):(snapshot.status.chat_text_chars??snapshot.status.text_chars);
  $('record-count').textContent='显示 '+rows.length+' 条 · 本地共保存 '+snapshot.status.records+' 条，'+(kind==='flow'?'执行窗口 ':'对话窗口 ')+chars+' / '+snapshot.status.text_limit+' 字';
}
const chatImageModes=new Map(),chatImageLoaders=new WeakMap();
const chatImageObserver='IntersectionObserver' in window?new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;chatImageObserver.unobserve(entry.target);const load=chatImageLoaders.get(entry.target);chatImageLoaders.delete(entry.target);if(entry.target.isConnected&&load)load();}},{root:$('records'),rootMargin:'180px'}):null;
function releaseChatImageLoads(area){for(const slot of area.querySelectorAll('.chat-image-slot')){chatImageObserver?.unobserve(slot);chatImageLoaders.delete(slot);}}
function chatImageText(text){return String(text||'').replace(/\[图片(?:\d*|，(?:未读取内容|读取中|读取失败)|摘要[:：][^\]]*)\]|\[已发送图片或表情\]|\[(?:商城表情|表情包)[:：](?:[^\[\]]|\[[^\]]*\])*\]/g,' ').trim();}
function chatMediaKey(group,row){return group+':'+row.message_id;}
function chatMode(group,row){return chatImageModes.get(chatMediaKey(group,row))||snapshot.status.chat_image_display||'image';}
function renderChatImage(row,image,index,group){
  const slot=node('div',undefined,'chat-image-slot');slot.append(node('span','正在加载图片…','chat-image-placeholder'));
  const failed=error=>{if(!slot.isConnected)return;const fallback=image.summary?.description||image.summary?.visible_text||[...String(row.text||'').matchAll(/\[(?:图片摘要|表情包|商城表情)[:：]([^\]]*)\]/g)][index]?.[1]||'原图暂不可用';const retry=node('button','重试','chat-image-retry');retry.type='button';retry.onclick=event=>{event.stopPropagation();slot.replaceChildren(node('span','正在加载图片…','chat-image-placeholder'));load(true);};slot.replaceChildren(node('span',fallback,'chat-image-fallback'),retry);slot.title=error.message;};
  const load=async(force=false)=>{
    try{const url=await messageImageURL(group,row,image,force);if(!slot.isConnected)return;const img=node('img',undefined,'chat-message-image');img.alt=image.summary?.description||'消息图片 '+(index+1);img.decoding='async';img.onerror=()=>failed(Error('浏览器无法解码原图'));img.src=url;const zoom=node('button','放大原图','chat-image-zoom');zoom.type='button';zoom.onclick=event=>{event.stopPropagation();showOriginal(url,'消息 '+row.message_id+' · 原图 '+(index+1));};slot.replaceChildren(img,zoom);}
    catch(error){failed(error);}
  };
  if(chatImageObserver){chatImageLoaders.set(slot,load);chatImageObserver.observe(slot);}else queueMicrotask(()=>load());
  return slot;
}
function messageRow(row){
  const group=selected,sent=row.kind==='sent',name=sent?(snapshot.status.name||'Bot'):(row.sender||'群友');let pictures=row.image_info||[];
  const item=node('div',undefined,'msg '+row.kind);item.title=eventSummary(row);
  const body=node('div',undefined,'msg-body'),head=node('div',undefined,'msg-head');head.append(node('strong',name),node('span',eventTime(row)));if(row.blocked)head.append(node('span','屏蔽','member-block-badge'));
  const bubble=node('div',undefined,'bubble'),toggle=pictures.length?node('button',undefined,'chat-media-toggle'):null;
  const render=()=>{
    releaseChatImageLoads(bubble);bubble.replaceChildren();if(row.quote)bubble.append(node('span',quoteSummary(row),'bubble-quote'));
    const imageMode=pictures.length&&chatMode(group,row)==='image';bubble.classList.toggle('image-bubble',Boolean(imageMode));
    if(imageMode){const words=chatImageText(row.text);if(words)bubble.append(node('span',words));const gallery=node('div',undefined,'chat-image-gallery');pictures.forEach((image,index)=>gallery.append(renderChatImage(row,image,index,group)));bubble.append(gallery);}
    else bubble.append(document.createTextNode(row.text||'[空消息]'));
    if(toggle){toggle.textContent=imageMode?'显示文字':'显示图片';toggle.setAttribute('aria-label',(imageMode?'显示文字摘要':'显示图片')+'，消息 '+row.message_id);}
  };
  if(toggle){const change=()=>{chatImageModes.set(chatMediaKey(group,row),chatMode(group,row)==='image'?'text':'image');while(chatImageModes.size>1000)chatImageModes.delete(chatImageModes.keys().next().value);render();};toggle.type='button';toggle.title='双击消息气泡也可以切换；不修改记录或上下文';toggle.onclick=event=>{event.stopPropagation();change();};bubble.title='双击切换图片 / 文字摘要';bubble.ondblclick=event=>{if(event.target.closest('button'))return;event.preventDefault();event.stopPropagation();change();};}
  render();body.append(head,bubble);
  const tags=[];if(pictures.length)tags.push('含 '+pictures.length+' 张图片');if(row.manual)tags.push('手动发送');if(row.historical)tags.push('历史恢复');if(row.level&&row.level!=='INFO')tags.push(row.level);
  if(tags.length||toggle){const line=node('div',undefined,'msg-tags');for(const tag of tags)line.append(node('span',tag));if(toggle)line.append(toggle);body.append(line);}
  item.updateRecord=latest=>{row=latest;pictures=latest.image_info||[];};
  const avatar=avatarNode('user',row.user_id,name,'avatar');
  if(/^[1-9]\d{4,14}$/.test(String(row.user_id||''))){avatar.classList.add('member-avatar-button');avatar.setAttribute('role','button');avatar.tabIndex=0;avatar.setAttribute('aria-label','查看并标记群友 '+name);const open=event=>{event.stopPropagation();openMember(row.user_id,name).catch(error=>toast(error.message,true));};avatar.onclick=open;avatar.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();open(event);}};}
  item.append(avatar,body);return item;
}
function flowRow(row){const item=node('div',undefined,'flow '+row.kind);item.title=eventSummary(row);item.append(node('span',eventTime(row),'flow-time'),node('span',(labels[row.kind]||row.kind)+(row.role?' · '+row.role:''),'kind-chip '+levelClass(row)),node('span',eventSummary(row)||'—','flow-text'));return item;}
function jsonView(target,value){target.replaceChildren();const json=JSON.stringify(value,null,2),regex=/("(?:\\.|[^"\\])*"\s*:?)|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi;let offset=0;for(const match of json.matchAll(regex)){target.append(document.createTextNode(json.slice(offset,match.index)));target.append(node('span',match[0],match[1]?(match[0].endsWith(':')?'json-key':'json-string'):match[2]?'json-literal':'json-number'));offset=match.index+match[0].length;}target.append(document.createTextNode(json.slice(offset)));}
function renderDetail(row){$('detail').hidden=!row;$('detail-empty').hidden=Boolean(row);document.querySelector('.chat-layout').classList.toggle('has-detail',Boolean(row));if(!row)return;$('detail-type').textContent=(labels[row.kind]||row.kind)+(row.role?' · '+row.role:'');$('detail-type').className='kind-chip '+levelClass(row);const props=[['时间',(eventDate(row)+' '+eventTime(row)).trim(),false],['发送者',row.kind==='sent'?(snapshot.status.name||'Bot'):(row.sender||'—'),false],['消息 ID',row.message_id||'—',true],['上下文 ID',contextID(row),true],['引用原 ID',row.reply_id||'—',true],['日志级别',row.level||'INFO',true],['来源',row.historical?'历史恢复':'实时记录',false]];$('detail-properties').replaceChildren();for(const [label,value,code] of props)$('detail-properties').append(node('dt',label),node('dd',String(value),code?'code':''));$('detail-content').textContent=row.text||'（没有文字内容）';renderQuote(row);renderImages(row);jsonView($('detail-json'),row);const valid=['received','sent'].includes(row.kind)&&/^-?\d+$/.test(row.message_id||'');$('delete-event').hidden=!valid;$('copy-id').disabled=contextID(row)==='—';$('copy-id').onclick=async()=>{try{await navigator.clipboard.writeText(contextID(row));toast('已复制 ID：'+contextID(row));}catch{toast('浏览器不允许复制，请在“原始数据”里手动选中 ID',true);}};$('delete-event').onclick=()=>{if(confirm('删除这条本地记录和 Bot 上下文？\nQQ 里的原消息不会撤回；当前群会短暂停止后自动恢复。'))act({action:'delete',message_id:row.message_id},$('delete-event')).catch(()=>{});};}
function closeDetail(){selectedEvent=null;document.querySelectorAll('#records [role=option]').forEach(item=>{item.classList.remove('selected');item.setAttribute('aria-selected','false');});renderDetail(null);}
$('detail-close').onclick=closeDetail;

function chips(value){const box=node('div',undefined,'stat-chips'),entries=Object.entries(value||{});if(!entries.length)return node('span','—');for(const [key,count] of entries)box.append(node('span',key+' '+(typeof count==='object'?JSON.stringify(count):count)));return box;}
function clock(value){if(!value)return '—';const date=typeof value==='number'?new Date(value*1000):new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',shanghai);}
function groupState(s,group){if(s.enabled===false||group?.enabled===false)return ['未启用','warn'];if(group?.stopped||s.stopped)return ['已暂停','warn'];if(s.error)return ['运行异常','bad'];if(s.waiting_for_qq)return ['等待 QQ 登录','warn'];if(!s.pid&&!s.online)return ['等待启动','warn'];if(!s.online)return ['等待 QQ 连接','warn'];return ['运行中','ok'];}
function renderStatus(){const s=snapshot.status,group=snapshot.groups.find(group=>group.id===selected),[stateText,stateTone]=groupState(s,group),global=['system','stickers'].includes(currentTab);
  $('group-title').textContent=global?(currentTab==='system'?'所有群总览':'共享表情库'):group?.name||String(selected);
  $('group-actions').hidden=global;$('group-tabs').hidden=global;document.querySelector('.group-meta').hidden=global;
  $('group-status').textContent=stateText;$('group-status').className='status-pill '+stateTone;$('group-status').title=s.enabled===false||group?.enabled===false?'前往行为设置 → 身份与开关，启用机器人':s.error||'';
  $('group-subtitle').textContent='群号 '+selected;$('session-detail').textContent=s.model+' · '+s.effort+(s.fast?' · Fast':'')+(s.no_cooldown?' · 无冷却':'');
  $('pause').hidden=Boolean(group?.stopped)||s.enabled===false||group?.enabled===false||!s.pid;$('restart').textContent=group?.stopped?'恢复运行':s.pid?'重启':'启动';$('restart').disabled=s.enabled===false||group?.enabled===false;$('restart').title=$('restart').disabled?'请先在行为设置中启用机器人':'';
  $('footer-model').textContent=s.model;if($('cooldown')){$('cooldown').checked=!s.no_cooldown;$('cooldown').closest('.edit').querySelector('.switch-label').textContent=s.no_cooldown?'关闭':'开启';}
  if(currentTab!=='status')return;
  const gate=s.participation||{},gateReasons={no_new_messages:'没有待处理的新消息',waiting_messages:'等待更多新消息',frequency_disabled:'参与强度为 0',cooldown:'水群冷却中',planner_backoff:'规划间隔等待',model_budget:'模型额度用尽',reply_budget:'发送额度用尽',direct_call_priority:'先处理点名消息',text_job_running:'上一轮还在处理',no_usable_hook:'没有适合的接话点',message_threshold:'已达到消息门槛',idle_compensation:'群聊空窗补偿',dynamic_score:'动态评分通过'};
  const qqUnchecked=s.enabled===false||group?.enabled===false||group?.stopped||(!s.pid&&!s.online);
  const vitals=[['QQ 连接',qqUnchecked?'未检测':s.online?'在线':s.waiting_for_qq?'等待登录':'连接中',qqUnchecked?'':s.online?'ok':'warn',qqUnchecked?'群后台尚未运行，不能据此判断 NapCat 是否在线':s.waiting_for_qq?'等待 QQ 登录':''],['Bot 后台',stateText,stateTone,s.pid&&!group?.stopped?'进程 '+s.pid:s.error||''],['今日模型调用',String(s.model_calls_today??0),'',s.model],['本轮参与强度',Math.round((gate.frequency??gate.probability??s.rate)*100)+'%','',gateReasons[gate.reason]||gate.reason||'等待新消息']];
  const cleanup=s.image_cleanup||{},cleanupNames={interval:'按分钟间隔',daily:'每日定时',expiry:'每张图片单独到期',windows:'Windows 任务计划程序'};
  const sections=[['连接与运行',[['最后一次轮询',clock(s.last_poll)],['冷却模式',s.no_cooldown?'无冷却':'按设置冷却'],['服务模式',s.fast?'Fast':'Standard'],['思考等级',s.effort]]],
    ['正在聊什么',[['当前话题',s.conversation_state?.topic?.text||'—'],['在等谁回应',s.conversation_state?.awaiting?.user_id||'—'],['进行中的任务',(s.conversation_state?.tasks||[]).map(task=>task.lane+' / '+task.stage).join('，')||'—'],['七天内交流',(s.conversation_state?.exchanges||0)+' 次']]],
    ['自动水群',[['触发方式',gate.mode==='dynamic'?'动态参与评分':'消息门槛 + 空窗补偿'],['待处理 / 门槛',(gate.pending_count??0)+' / '+(gate.threshold??'—')],['本轮判断',gateReasons[gate.reason]||gate.reason||'等待新消息'],['最近结果',chips(s.participation_outcomes)]]],
    ['记录与记忆',[['本页记录',s.records+' 条'],['记录文字量',s.text_chars+' / '+s.text_limit+' 字'],['模型输入上限',s.prompt_limit+' 字'],['图片摘要',s.image_summaries+' 张'],['已观察消息',s.observed_messages+' 条'],['已归档话题',s.memory_stats?.episodes||0],['已修订事实',s.memory_stats?.revised_facts||0],['群记忆',chips(s.memory_stats)]]],
    ['生成图片清理',[['清理方式',cleanupNames[cleanup.mode]||'—'],['图片保留',(cleanup.ttl_hours??2)+' 小时'],['间隔 / 每日时间',(cleanup.interval_minutes??120)+' 分钟 / '+(cleanup.daily_time||'03:00')],['上次检查',clock(cleanup.last_checked)],['下次检查',cleanup.mode==='windows'?'由 Windows 任务触发':clock(cleanup.next_due)],['上次清理',(cleanup.removed??0)+' 张'],['存放位置','群运行目录/temp/ai-images']]]];
  const strip=node('div',undefined,'vitals');for(const [label,value,tone,note] of vitals){const box=node('div',undefined,'vital');box.append(node('div',label,'vital-label'),node('div',value,'vital-value'+(tone?' '+tone:'')));if(note)box.append(node('div',note,'vital-note'));strip.append(box);}
  const grid=node('div',undefined,'status-sections');for(const [title,fields] of sections){const card=node('section',undefined,'status-card');card.append(node('h3',title));for(const [label,value] of fields){const row=node('div',undefined,'status-row'),cell=node('div',undefined,'value');cell.append(value instanceof Node?value:document.createTextNode(String(value)));row.append(node('div',label,'label'),cell);card.append(row);}grid.append(card);}
  $('status').replaceChildren(strip,grid);}
async function poll(force=false){if(closed||(polling&&!force))return;polling=true;const ticket=++pollSequence;try{const params=new URLSearchParams({kind,q:$('record-search').value});if(selected!==null)params.set('group',selected);const result=await api('/api/snapshot?'+params);if(ticket!==pollSequence)return;backendReady=result.ui_revision===2;showBackendVersion(backendReady);snapshot=result;selected=snapshot.selected;renderGroups();renderRecords();renderStatus();renderComposer();checkManualSend(selected);$('intervention-count').textContent=snapshot.status.intervention_count??0;$('connection').textContent='已连接';setDot('connection-dot',true);}catch(error){if(ticket!==pollSequence)return;if(error.message.includes('白名单')){selected=null;lastEvents='';}else{$('connection').textContent='连接断开';setDot('connection-dot',false,true);toast(error.message,true);}}finally{if(ticket===pollSequence)polling=false;}}

async function loadConfig(){const ticket=++configSequence,scope=activeScope,group=selected;const result=await api('/api/config?'+new URLSearchParams({scope,group}));if(ticket!==configSequence||scope!==activeScope||group!==selected)return;configData=result;renderConfig();applyPromptEditor();document.querySelectorAll('.scope-switch [data-scope]').forEach(button=>button.classList.toggle('active',button.dataset.scope===scope));$('scope-description').textContent=scope==='group'?'只影响群 '+group+'。留空或选“使用全局”的项目继续跟随全局默认':'所有没有单独设置的群都使用这里的值';}
function modelOptions(input,current,field){const options=field==='model'?configData.models.map(model=>model.name):(configData.models.find(model=>model.name===configData.config.model)?.efforts||['low','medium','high']);for(const value of new Set([current,...options])){const names={none:'不思考',minimal:'极低',low:'低',medium:'中',high:'高',xhigh:'很高',max:'最高',ultra:'极高'};const option=node('option',field==='reasoning_effort'?(names[value]||value)+'（'+value+'）':value);option.value=value;input.append(option);}input.value=current;}
async function saveSetting(key,raw,{scope,group,type,isObject,draftKey},button){try{let next=raw;if(scope==='group'&&(!String(next).trim()||next==='inherit')){await act({action:'config_inherit',scope,group,key},button);}else{if(isObject)next=JSON.parse(next);else if(type==='boolean')next=next==='on';else if(type==='number'){next=Number(next);if(!String(raw).trim()||!Number.isFinite(next))throw Error('请输入有效的数字');}await act({action:'config',scope,group,key,value:next},button);}if(configDrafts[draftKey]===raw)delete configDrafts[draftKey];await loadConfig();}catch(error){toast(error.message,true);if(configData)renderConfig();}}
function settingRow(key){const cfg=configData.config,meta=configData.fields[key],scope=activeScope,group=selected,draftKey=scope+':'+(scope==='group'?group:'*')+':'+key;const value=cfg[key],local=scope==='group',overridden=Object.hasOwn(configData.overrides,key),inherited=local&&!overridden,locked=local&&configData.global_only.includes(key),type=typeof value,isObject=type==='object',ctx={scope,group,type,isObject,draftKey};
  const box=node('div',undefined,'setting'+(inherited?' inherited':''));box.id='setting-'+key;const label=node('div'),title=node('div',meta.label,'setting-title');if(local)title.append(node('span',locked?'只能全局设置':overridden?'本群单独设置':'使用全局','scope-badge'+(overridden?' override':'')));label.append(title);if(meta.help){const help=node('div',meta.help,'setting-help');help.title='点击展开 / 收起';help.onclick=()=>help.classList.toggle('open');label.append(help);}label.append(node('div',key,'key'));box.append(label);
  const edit=node('div',undefined,'edit'),save=node('button','保存','save primary');save.type='button';save.disabled=locked;let input,read=()=>input.value;
  const markDirty=()=>{configDrafts[draftKey]=read();edit.classList.add('dirty');};
  const immediate=element=>{element.onchange=()=>saveSetting(key,read(),ctx,element);};
  const inheritOption=text=>{const option=node('option','使用全局（'+text+'）');option.value='inherit';return option;};
  if(type==='boolean'&&!local){const sw=node('label',undefined,'switch'),state=node('span',value?'开启':'关闭','switch-label');input=node('input');input.type='checkbox';input.checked=Boolean(value);input.setAttribute('role','switch');sw.append(input,node('span'));read=()=>input.checked?'on':'off';input.onchange=()=>{state.textContent=input.checked?'开启':'关闭';saveSetting(key,read(),ctx,input);};edit.append(state,sw);}
  else if(type==='boolean'||meta.options||['model','reasoning_effort'].includes(key)){input=node('select');if(type==='boolean'){if(local)input.append(inheritOption(configData.global_config[key]?'开启':'关闭'));for(const choice of ['on','off']){const option=node('option',choice==='on'?'开启':'关闭');option.value=choice;input.append(option);}input.value=inherited?'inherit':value?'on':'off';}else if(meta.options){if(local)input.append(inheritOption(meta.options[configData.global_config[key]]||configData.global_config[key]));for(const [choice,text] of Object.entries(meta.options)){const option=node('option',text);option.value=choice;input.append(option);}input.value=inherited?'inherit':value;}else{modelOptions(input,value,key);if(local){input.prepend(inheritOption(configData.global_config[key]));if(inherited)input.value='inherit';}}immediate(input);edit.append(input);}
  else if(isObject||key.startsWith('member_prompt_')||key==='member_model_block_rules'){input=node('textarea');input.spellcheck=false;input.value=inherited?'':isObject?JSON.stringify(value,null,2):value;input.placeholder=isObject?JSON.stringify(configData.global_config[key],null,2):String(configData.global_config[key]||'可留空');input.oninput=markDirty;edit.append(input,save);}
  else if(type==='number'&&/probability|confidence|strength/.test(key)){const wrap=node('div',undefined,'slider'),range=node('input');range.type='range';range.min='0';range.max='1';range.step='0.01';input=node('input');input.type='number';input.min='0';input.max='1';input.step='any';input.value=inherited?'':value;input.placeholder=key==='webui_key'?'留空则启动时随机生成':String(configData.global_config[key]);range.value=input.value===''?configData.global_config[key]:input.value;range.setAttribute('aria-label',meta.label);range.oninput=()=>{input.value=range.value;markDirty();};input.oninput=()=>{if(input.value!=='')range.value=input.value;markDirty();};wrap.append(range,input);edit.append(wrap,save);}
  else{input=node('input');input.type=key==='webui_key'?'password':type==='number'?'number':'text';input.value=inherited?'':value;input.placeholder=key==='webui_key'?'留空则启动时随机生成':String(configData.global_config[key]);if(type==='number'){input.min='0';input.step='any';}input.oninput=markDirty;edit.append(input,save);}
  if(Object.hasOwn(configDrafts,draftKey)&&input.type!=='checkbox'&&input.tagName!=='SELECT'){input.value=configDrafts[draftKey];edit.classList.add('dirty');const range=edit.querySelector('input[type=range]');if(range&&input.value!=='')range.value=input.value;}
  if(input.tagName==='INPUT'&&input.type!=='checkbox')input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();save.click();}};
  input.disabled=locked;input.dataset.field=key;input.setAttribute('aria-label',meta.label);save.onclick=()=>saveSetting(key,read(),ctx,save);
  if(local&&overridden){const reset=node('button','改回全局','inherit-setting');reset.type='button';reset.title='删除这个群的单独设置，恢复使用全局值';reset.onclick=async()=>{try{const raw=configDrafts[draftKey];await act({action:'config_inherit',scope:'group',group,key},reset);if(configDrafts[draftKey]===raw)delete configDrafts[draftKey];await loadConfig();}catch{}};edit.append(reset);}
  box.append(edit);return box;}
function cooldownRow(){const box=node('div',undefined,'setting'),label=node('div');label.append(node('div','回复冷却','setting-title'),node('div','关闭后不等待点名冷却和水群冷却，但仍受每小时/每天额度限制。只作用于当前群的运行状态。','setting-help'));const edit=node('div',undefined,'edit'),sw=node('label',undefined,'switch'),input=node('input'),on=!snapshot?.status.no_cooldown;input.type='checkbox';input.id='cooldown';input.checked=on;input.setAttribute('role','switch');input.setAttribute('aria-label','回复冷却');sw.append(input,node('span'));input.onchange=()=>act({action:'config',key:'cooldown',scope:activeScope,value:input.checked?'on':'off'},input).catch(()=>{input.checked=!input.checked;});edit.append(node('span',on?'开启':'关闭','switch-label'),sw);box.append(label,edit);return box;}
function renderConfig(){const term=$('config-search').value.trim().toLowerCase(),area=$('settings'),scroll=area.scrollTop,cfg=configData.config;const priority=['enabled','bot_name','bot_aliases','model','reasoning_effort','fast_mode','passive_probability','sticker_reply_probability','chat_image_display','at_reply_probability','quote_reply_probability','proactive_topic_probability'];const keys=[...priority.filter(key=>key in cfg),...Object.keys(cfg).filter(key=>!priority.includes(key))].filter(key=>{const meta=configData.fields[key];return (meta.label+' '+key+' '+meta.help).toLowerCase().includes(term);});const buckets=new Map(categories.map(item=>[item[0],[]]));for(const key of keys)buckets.get(categories.find(item=>item[3](key))[0]).push(key);area.replaceChildren();$('settings-index').replaceChildren();for(const [id,title,description] of categories){const list=buckets.get(id),showCooldown=id==='participation'&&(!term||'回复冷却 cooldown'.includes(term));if(!list.length&&!showCooldown)continue;const section=node('section',undefined,'setting-group');section.id='settings-'+id;section.append(node('h3',title));if(description)section.append(node('p',description));if(showCooldown)section.append(cooldownRow());for(const key of list)section.append(settingRow(key));area.append(section);const jump=node('button');jump.type='button';jump.title=title;jump.append(node('span',title,'settings-index-label'),node('span',String(list.length+(showCooldown?1:0)),'count'));jump.onclick=()=>section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});$('settings-index').append(jump);}if(!area.children.length)area.append(node('div','没有找到包含“'+term+'”的设置','empty'));area.scrollTop=scroll;}

async function switchTab(tab){stashPromptDraft();currentTab=tab;document.querySelectorAll('[data-context-tabs] button').forEach(item=>{const active=item.dataset.tab===tab;item.classList.toggle('active',active);item.setAttribute('aria-selected',String(active));});document.querySelectorAll('.side-nav [data-view]').forEach(item=>item.classList.toggle('active',item.dataset.view===tab));document.querySelectorAll('.tab').forEach(item=>item.classList.toggle('active',item.id==='tab-'+tab));try{if(snapshot)renderStatus();await loadActivePane();}catch(error){toast(error.message,true);}}
async function loadActivePane(){if(['settings','persona'].includes(currentTab))await loadConfig();else if(currentTab==='database')await loadDatabase();else if(currentTab==='stickers')await loadStickers();else if(currentTab==='system')await loadSystem();else if(currentTab==='members')await loadMembers();}
document.querySelectorAll('[data-context-tabs] [data-tab]').forEach(button=>button.onclick=()=>switchTab(button.dataset.tab));
document.querySelectorAll('.side-nav [data-view]').forEach(button=>button.onclick=()=>switchTab(button.dataset.view));
document.querySelectorAll('[data-kind]').forEach(button=>button.onclick=()=>{kind=button.dataset.kind;document.querySelectorAll('[data-kind]').forEach(item=>item.classList.toggle('active',item===button));poll(true);});
$('group-search').oninput=()=>snapshot&&renderGroups();$('record-search').oninput=()=>poll(true);$('config-search').oninput=()=>configData&&renderConfig();$('level-filter').onchange=()=>snapshot&&renderRecords();$('clear-search').onclick=()=>{$('record-search').value='';poll(true);};

function draftKey(){return activeScope+':'+(activeScope==='group'?selected:'all')+':'+activePrompt;}
function stashPromptDraft(){const editor=$('persona');if(editor.dataset.draftKey)promptDrafts[editor.dataset.draftKey]=editor.value;}
function promptState(text,dirty=false){$('persona-state').textContent=text;$('persona-state').classList.toggle('dirty',dirty);$('persona-count').textContent=$('persona').value.length+' 字';}
function applyPromptEditor(){if(!configData)return;const editor=$('persona'),key=draftKey(),local=activeScope==='group';editor.value=promptDrafts[key]??(local&&!configData.prompt_overrides[activePrompt]?'':configData[activePrompt]||'');editor.placeholder=local?(configData.global_prompts[activePrompt]||'全局提示词为空'):'在这里输入提示词';editor.dataset.draftKey=key;editor.classList.toggle('inherited-prompt',local&&!editor.value);$('inherit-prompt').hidden=!local||!configData.prompt_overrides[activePrompt];$('prompt-description').textContent=promptInfo[activePrompt]+(local?' 留空表示这个群使用全局版本（灰色文字即全局内容）。':'');document.querySelectorAll('[data-prompt]').forEach(button=>button.classList.toggle('active',button.dataset.prompt===activePrompt));promptState(configData.help,Object.hasOwn(promptDrafts,key)&&promptDrafts[key]!==(configData[activePrompt]||''));}
async function changeScope(scope){stashPromptDraft();activeScope=scope;await loadConfig();}
document.querySelectorAll('.scope-switch [data-scope]').forEach(button=>button.onclick=()=>changeScope(button.dataset.scope).catch(error=>toast(error.message,true)));
$('save-persona').onclick=async()=>{const kind=activePrompt,text=$('persona').value,scope=activeScope,key=draftKey();try{await act(scope==='group'&&!text.trim()?{action:'config_inherit',scope,key:kind}:{action:kind,scope,text},$('save-persona'));if(scope==='group'&&!text.trim())delete promptDrafts[key];else promptDrafts[key]=text;await loadConfig();}catch{}};
$('inherit-prompt').onclick=async()=>{if(!confirm('删除这个群单独的提示词，改回使用全局版本？'))return;try{const key=draftKey();await act({action:'config_inherit',scope:'group',key:activePrompt},$('inherit-prompt'));delete promptDrafts[key];await loadConfig();}catch{}};
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{stashPromptDraft();activePrompt=button.dataset.prompt;applyPromptEditor();});
$('persona').oninput=()=>promptState('有未保存的修改',true);

$('white-form').onsubmit=event=>{event.preventDefault();const id=$('white-id').value.trim();if(!/^\d+$/.test(id))return toast('请输入纯数字的群号',true);act({action:'whitelist',mode:'add',group_id:id},$('white-add')).then(()=>$('white-id').value='').catch(()=>{});};
$('white-remove').onclick=()=>{if(confirm('把这个群移出白名单，并停止这个群的 Bot？'))act({action:'whitelist',mode:'remove',group_id:String(selected)},$('white-remove')).catch(()=>{});};
$('pause').onclick=()=>act({action:'pause'},$('pause')).catch(()=>{});$('restart').onclick=()=>act({action:'restart'},$('restart')).catch(()=>{});

function consoleLog(text){const out=$('command-output');out.textContent+=(out.textContent?'\n\n':'')+text;out.scrollTop=out.scrollHeight;}
$('open-console').onclick=()=>{$('console-dialog').showModal();$('command').focus();};$('console-close').onclick=()=>$('console-dialog').close();
$('command-form').onsubmit=event=>{event.preventDefault();const command=$('command').value.trim();if(!command)return;consoleLog('> '+command);act({action:'command',command},$('command-form').querySelector('button')).then(data=>{consoleLog(data.message||'已完成');$('command').value='';}).catch(error=>consoleLog(error.message));};
$('command').onkeydown=event=>{if(event.key!=='Tab')return;event.preventDefault();const input=$('command'),text=input.value,parts=text.replace(/^\//,'').split(/\s+/);let choices=['status','config','groups','group','whitelist','search','view','delete','help','restart','exit','quit','stop','system','say'];if(parts.length>1){const head=parts[0];choices=head.startsWith('co')?['show','set','get','local','global','inherit',...Object.keys(configData?.config||{}),...Object.keys(titles),'name','aliases','rate','effort','fast','text_limit','prompt']:head.startsWith('wh')?['add','remove','show']:head.startsWith('vi')?['all','chat','flow','system']:head.startsWith('sy')?['add','list','remove','delete','clear','--scope','group','all','--mode','context','once','timed','persistent','--minutes']:[];}const prefix=parts.at(-1);const hits=[...new Set(choices)].filter(choice=>choice.startsWith(prefix));if(hits.length===1)input.value=text.slice(0,text.length-prefix.length)+hits[0]+' ';else if(hits.length)consoleLog('可选：'+hits.join('  '));};
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&selectedEvent&&!document.querySelector('dialog[open]'))closeDetail();if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||document.querySelector('dialog[open]'))return;if(['ArrowLeft','ArrowRight'].includes(event.key)&&snapshot?.groups.length&&!['system','stickers'].includes(currentTab)){event.preventDefault();const groups=snapshot.groups.map(group=>group.id),index=groups.indexOf(selected);selectGroup(groups[(index+(event.key==='ArrowRight'?1:groups.length-1))%groups.length]);}});
$('open-intervention').onclick=()=>{if(!snapshot?.features?.includes('system_interventions'))return toast('请在 CLI 输入 /exit，再手动启动 Debug 以加载系统干预功能',true);$('intervention-dialog').showModal();$('intervention-window').textContent='当前群的对话窗口：'+snapshot.status.text_limit+' 字符；执行日志不消耗有效期';loadInterventions();};
$('intervention-close').onclick=()=>$('intervention-dialog').close();
$('intervention-lifetime').onchange=()=>{$('intervention-minutes-row').hidden=$('intervention-lifetime').value!=='timed';};
$('intervention-form').onsubmit=async event=>{event.preventDefault();const group=selected,text=$('intervention-text').value;try{await act({action:'system',mode:'add',group,text,target_scope:$('intervention-scope').value,lifetime:$('intervention-lifetime').value,minutes:Number($('intervention-minutes').value)},$('intervention-save'));$('intervention-text').value='';await loadInterventions();}catch{}};
$('intervention-clear').onclick=async()=>{try{await act({action:'system',mode:'clear',target_scope:'group'},$('intervention-clear'));await loadInterventions();}catch{}};
async function loadInterventions(){try{const group=selected,data=await api('/api/interventions?group='+group);if(group!==selected)return;const box=$('intervention-list');box.replaceChildren();for(const entry of data.entries.toReversed()){const row=node('div',undefined,'intervention-record');row.append(node('strong',interventionStates[entry.status]+' · '+interventionModes[entry.mode]),node('p',entry.text),node('small',entry.id+' · '+clock(entry.created)+(entry.mode==='context'?' · 已经过 '+entry.used_chars+' 字符':'')));if(entry.status==='active'){const clear=node('button','清除');clear.type='button';clear.onclick=async()=>{try{await act({action:'system',mode:'remove',id:entry.id,group},clear);await loadInterventions();}catch{}};row.append(clear);}row.append(interventionDelete(entry,group));box.append(row);}if(!data.entries.length)box.append(node('div','还没有系统干预','empty'));}catch(error){toast(error.message,true);}}
if(!token){$('connection').textContent='未授权';toast('请在 CLI 输入 /webui，用 CLI 显示的链接打开这个页面',true);}else{poll();setInterval(()=>poll(),1500);}

function quoteSummary(row){const q=row.quote;return q?.found?'回复 '+(q.sender||'群友')+'：'+(q.text||'[空消息]'):'回复的消息找不到了';}
function renderQuote(row){const box=$('detail-quote');box.hidden=!row.quote;box.replaceChildren();if(!row.quote)return;box.append(node('h3','回复的消息'));const q=row.quote;if(!q.found){box.append(node('div','本地记录里找不到这条原消息','hint'));}else box.append(node('div',q.sender||'群友','quote-sender'),node('div',q.text||'[空消息]','quote-body'));if(row.reply_id){box.append(node('div','原消息 ID '+row.reply_id,'code quote-source-id'));const button=node('button','在记录里找到原消息','quote-jump');button.type='button';button.onclick=()=>{$('record-search').value=row.reply_id;kind='chat';document.querySelectorAll('[data-kind]').forEach(item=>item.classList.toggle('active',item.dataset.kind==='chat'));poll(true);};box.append(button);}}

let backendReady=null;
let databaseData=null,dbOffset=0,dbSequence=0,stickerData=null,stickerOffset=0,stickerSequence=0,editorSave=null;
const imageURLs=[];
const columnTitles={id:'编号',kind:'类型',content:'记忆 / 表达内容',subject:'关联群友 ID',keywords:'召回关键词 JSON',evidence:'来源证据 JSON',stamp:'时间戳',count:'观察次数',speaker:'群友 ID',text:'原始来源文本',learned:'已学习（0 / 1）',name:'显示名字',note_id:'关联记忆 ID',confidence:'置信度',fact_key:'事实类别',uses:'使用次数',positive:'正向反馈',negative:'负向反馈',last_used:'最后使用时间',old_id:'旧事实 ID',new_id:'新事实 ID'};

const originalURLs=new Map();
const emotionOptions=['开心','无语','疑惑','震惊','难过','赞同','安慰','打招呼','撒娇','调侃','其他'];
async function imageURL(path){const response=await fetch(path,{headers:{'X-QQ-Debug-Key':token}});if(!response.ok){let reason='原图暂时不可用';try{reason=(await response.json()).error||reason;}catch{}throw Error(reason);}return URL.createObjectURL(await response.blob());}
const messageImageRequests=new Map(),imageFetchQueue=[];let activeImageFetches=0;
function imageFetchSlot(task){return new Promise((resolve,reject)=>{imageFetchQueue.push({task,resolve,reject});pumpImageFetches();});}
function pumpImageFetches(){while(activeImageFetches<3&&imageFetchQueue.length){const {task,resolve,reject}=imageFetchQueue.shift();activeImageFetches++;Promise.resolve().then(task).then(resolve,reject).finally(()=>{activeImageFetches--;pumpImageFetches();});}}
async function messageImageURL(group,row,image,force=false){
  const key=group+':'+row.message_id+':'+image.key;if(originalURLs.has(key)&&!force)return originalURLs.get(key);
  if(force&&originalURLs.has(key)){URL.revokeObjectURL(originalURLs.get(key));originalURLs.delete(key);}
  const old=messageImageRequests.get(key);if(old?.promise)return old.promise;if(!force&&old?.error&&Date.now()-old.time<30000)throw old.error;
  const promise=imageFetchSlot(()=>imageURL('/api/message-image?'+new URLSearchParams({group,message_id:row.message_id,key:image.key})))
    .then(url=>{originalURLs.set(key,url);messageImageRequests.delete(key);while(originalURLs.size>80){const [first,value]=originalURLs.entries().next().value;originalURLs.delete(first);URL.revokeObjectURL(value);}return url;})
    .catch(error=>{messageImageRequests.set(key,{error,time:Date.now()});while(messageImageRequests.size>200){const first=messageImageRequests.keys().next().value;if(messageImageRequests.get(first).promise)break;messageImageRequests.delete(first);}throw error;});
  messageImageRequests.set(key,{promise});return promise;
}
function showOriginal(url,title){$('image-viewer-title').textContent=title;$('image-viewer-img').src=url;$('image-viewer-img').alt=title;$('image-viewer-img').classList.remove('actual-size');$('image-viewer').showModal();}
$('image-viewer-close').onclick=()=>$('image-viewer').close();
$('image-viewer-img').onclick=()=>$('image-viewer-img').classList.toggle('actual-size');
let editorSession=0,editorBusy=false,activePrefill=null;
function cancelPrefill(job){if(job)api('/api/action',{action:'sticker_prefill',mode:'cancel',group:job.group,id:job.id}).catch(()=>{});}
function resetPrefill(){editorSession++;editorBusy=false;cancelPrefill(activePrefill);activePrefill=null;$('store-ai').hidden=true;$('store-ai-status').hidden=true;$('store-ai-status').classList.remove('error');$('store-save').disabled=false;}
$('store-editor').addEventListener('close',()=>{resetPrefill();editorSave=null;});
async function encodeStickerFile(file){if(!file)throw Error('请先选择图片');if(file.size>2*1024*1024)throw Error('上传图片上限2MB');return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('读取图片失败'));reader.readAsDataURL(file);});}
function enablePrefill(inputs,options){
  const epoch=editorSession,button=$('store-ai'),status=$('store-ai-status');button.hidden=false;button.textContent='AI 识别填入';button.disabled=!snapshot?.features?.includes('sticker_ai_prefill');status.hidden=false;status.textContent=button.disabled?'请 /exit 后手动重开Debug，加载AI填入功能':'只填入建议，不保存、不入库。使用当前群识图模型和调用额度。';
  button.onclick=async()=>{
    if(editorBusy)return;const sourceToken=options.token(),before=Object.fromEntries(['label','description','emotion','use_when'].map(key=>[key,inputs[key].value]));
    const current=()=>epoch===editorSession&&$('store-editor').open;const job={id:crypto.randomUUID().replaceAll('-',''),group:options.group};activePrefill=job;editorBusy=true;button.disabled=true;button.textContent='识别中…';$('store-save').disabled=true;status.classList.remove('error');status.textContent='正在准备图片，结果只会填入表单…';
    try{
      const payload=await options.prepare();if(!current())return;if(options.token()!==sourceToken)throw Error('图片已更换，请重新点击AI识别填入');
      await api('/api/action',{action:'sticker_prefill',group:options.group,request_id:job.id,...payload});
      if(!current()){cancelPrefill(job);return;}status.textContent='AI正在识别，请稍候；完成后仍需手动保存。';
      const deadline=Date.now()+240000;
      while(current()&&Date.now()<deadline){
        const result=await api('/api/sticker-prefill?'+new URLSearchParams({group:job.group,id:job.id}));
        if(!current())return;
        if(options.token()!==sourceToken)throw Error('图片已更换，旧图片识别结果已忽略，请重新点击');
        if(result.status==='done'){
          let preserved=0;for(const key of ['label','description','emotion','use_when']){if(inputs[key].value===before[key])inputs[key].value=result.values[key];else preserved++;}
          status.textContent='已填入AI建议'+(preserved?'，保留了等待期间手动修改的字段':'')+'。请检查后点击保存；目前尚未入库。';return;
        }
        if(['failed','cancelled'].includes(result.status))throw Error(result.error||'AI识别已取消');
        await new Promise(resolve=>setTimeout(resolve,800));
      }
      if(current())throw Error('等待识别超时，请重试');
    }catch(error){cancelPrefill(job);if(current()){status.textContent=error.message;status.classList.add('error');}}
    finally{if(activePrefill===job)activePrefill=null;if(current()){editorBusy=false;button.disabled=false;button.textContent='AI 识别填入';$('store-save').disabled=false;}}
  };
}
function stickerFields(label='',description='',emotion='其他',use_when=''){return [{name:'label',label:'表情名称',value:label,maxLength:80},{name:'description',label:'图片说明（35字）',value:description,maxLength:35},{name:'emotion',label:'情绪分类',value:emotion,options:emotionOptions},{name:'use_when',label:'适用场景（30字）',value:use_when,maxLength:30}];}
function editImageSummary(row,image,group){const summary=image.summary||{};const fields=[{name:'description',label:'图片摘要（120字）',value:summary.description||'',long:true,maxLength:120},{name:'visible_text',label:'可见文字（140字）',value:summary.visible_text||'',long:true,maxLength:140},{name:'uncertainty',label:'不确定说明（70字）',value:summary.uncertainty||'',maxLength:70}];openEditor('修改图片摘要',fields,async inputs=>{const edited=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));await act({action:'image',mode:'summary',group,message_id:row.message_id,key:image.key,values:{summary:{...edited,confidence:1},expected_revision:image.summary_revision||''}},$('store-save'));},'只修改本群这张图片的理解。同图后续复用人工摘要，不触发回复，也不会自动收录表情。');}
function collectMessageImage(row,image,group){const fields=stickerFields((image.summary?.description||'群聊表情').slice(0,35),(image.summary?.description||'').slice(0,35));const inputs=openEditor('人工确认收录表情包',fields,async inputs=>{const values=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));await act({action:'image',mode:'collect',group,message_id:row.message_id,key:image.key,values},$('store-save'));},'确认后直接加入共享表情库，覆盖自动审核的拒绝结果；保留原图和GIF动画，不发送到群。');enablePrefill(inputs,{group,token:()=>group+':'+row.message_id+':'+image.key,prepare:async()=>({source:'message',message_id:row.message_id,key:image.key})});}
function renderImages(row){const area=$('detail-images');area.replaceChildren();area.hidden=!row.image_info?.length;if(area.hidden)return;area.append(node('h3','图片'));const group=selected;for(const image of row.image_info){const section=node('div',undefined,'image-state');section.append(node('div',(image.summary_available?(image.summary_origin==='operator'?'人工摘要':'已识别内容'):'还没有识别结果')+'，'+(image.sticker_state==='approved'?'已加入表情库':image.sticker_state==='rejected'?'自动审核未通过':'未加入表情库')),node('div','出现 '+image.seen_count+' 次，重复 '+image.repeat_count+' 次，最近 10 条里 '+image.recent_count+' 次，连续 '+image.consecutive_count+' 次'),node('div','识别结果还能用 '+image.remaining_messages+' / '+image.window_messages+' 条消息'));if(image.emotion)section.append(node('div','情绪：'+image.emotion+(image.use_when?'，适合 '+image.use_when:'')));if(image.asset_type)section.append(node('div','类型：'+image.asset_type));section.append(node('div',image.hash,'code'));const actions=node('div',undefined,'image-actions'),view=node('button','查看原图'),edit=node('button','修改摘要'),collect=node('button',image.sticker_state==='approved'?'已收录':'收录表情包');for(const button of [view,edit,collect]){button.type='button';button.disabled=!snapshot.features?.includes('image_management');if(button.disabled)button.title='请 /exit 后重启 Debug 后台';}collect.disabled=collect.disabled||image.sticker_state==='approved';view.onclick=async()=>{view.disabled=true;try{showOriginal(await messageImageURL(group,row,image),'消息 '+row.message_id+' · 原图');}catch(error){toast(error.message,true);}finally{view.disabled=false;}};edit.onclick=()=>editImageSummary(row,image,group);collect.onclick=()=>collectMessageImage(row,image,group);actions.append(view,edit,collect);section.prepend(actions);const local=originalURLs.get(group+':'+row.message_id+':'+image.key);if(local){const thumb=node('img',undefined,'message-image-thumb');thumb.src=local;thumb.alt='图片预览，点击查看原图';thumb.onclick=()=>showOriginal(local,'消息 '+row.message_id+' · 原图');section.prepend(thumb);}area.append(section);}}

async function loadSystem(){const data=await api('/api/system');$('system-summary').textContent=data.online_groups+' / '+data.total_groups+' 个群在线，今天共调用模型 '+data.today_model_calls+' 次，全局默认模型 '+data.global_model;$('global-status').replaceChildren();for(const group of data.groups){const panel=node('article',undefined,'overview-card'),head=node('header'),[text,tone]=groupState(group,group);head.append(avatarNode('group',group.id,group.name,'group-avatar',group.id),node('h3',group.name),node('span',text,'status-pill '+tone));const facts=node('dl');for(const [label,value] of [['群号',group.id],['人格',group.name_persona],['模型',group.model+' · '+group.effort+(group.fast?' · Fast':'')],['今日调用',group.model_calls_today+' 次'],['单独设置',group.override_count+' 项'],['最后轮询',clock(group.last_poll)],['进程',group.pid||'—']])facts.append(node('dt',label),node('dd',String(value)));panel.append(head,facts);if(group.error)panel.append(node('div',group.error,'error-note'));const open=node('button','打开这个群');open.type='button';open.onclick=()=>{selectGroup(group.id);switchTab('logs');};panel.append(open);$('global-status').append(panel);}if(!data.groups.length)$('global-status').append(node('div','还没有授权的群','empty'));}
$('system-refresh').onclick=()=>loadSystem().catch(error=>toast(error.message,true));

async function loadDatabase(){const ticket=++dbSequence,group=selected,table=$('db-table').value||'notes';const data=await api('/api/database?'+new URLSearchParams({group,table,q:$('db-search').value,offset:dbOffset}));if(ticket!==dbSequence||selected!==group)return;if(data.offset>0&&data.offset>=data.total){dbOffset=Math.max(0,Math.floor((data.total-1)/data.limit))*data.limit;return loadDatabase();}databaseData=data;updatePager('db',data);$('db-table').replaceChildren();for(const [key,label] of Object.entries(data.tables)){const option=node('option',label);option.value=key;$('db-table').append(option);}$('db-table').value=data.table;const area=$('db-records');area.replaceChildren();if(!data.rows.length)area.append(node('div',data.columns.length?($('db-search').value?'没有匹配的记录':'这张表还是空的'):'这个群还没有建立记忆数据库，聊过一段时间后会自动生成','empty'));else{const tableNode=node('table'),head=node('thead'),header=node('tr'),keys=data.columns.map(item=>item.name);for(const key of keys)header.append(node('th',columnTitles[key]||key));header.append(node('th','操作'));head.append(header);tableNode.append(head);const body=node('tbody');for(const row of data.rows){const tr=node('tr');for(const key of keys){const text=row[key]===null?'—':String(row[key]);const td=node('td',text.length>160?text.slice(0,160)+'…':text,/id|stamp|confidence|count|uses|learned/.test(key)?'code':'');td.title=text;tr.append(td);}const tools=node('td',undefined,'row-tools'),edit=node('button','编辑'),remove=node('button','删除','danger-text');edit.onclick=()=>openDatabaseEditor(row);remove.onclick=async()=>{if(!confirm('删除这条记忆以及依赖它的记录？\n删除前会自动备份，QQ 消息不受影响。'))return;try{await act({action:'database',mode:'delete',group,table:data.table,row_id:row._rowid,expected:row},remove);await loadDatabase();}catch{}};tools.append(edit,remove);tr.append(tools);body.append(tr);}tableNode.append(body);area.append(tableNode);}$('db-count').textContent='共 '+data.total+' 条 · 第 '+(Math.floor(data.offset/50)+1)+' / '+Math.max(1,Math.ceil(data.total/50))+' 页';$('db-prev').disabled=data.offset===0;$('db-next').disabled=data.offset+data.rows.length>=data.total;$('db-add').disabled=!data.columns.length;}

function openEditor(title,fields,save,note){resetPrefill();$('store-title').textContent=title;$('store-note').textContent=note||'保存前会自动备份';$('store-fields').replaceChildren();const inputs={};for(const field of fields){const row=node('div',undefined,'store-field'),label=node('label',field.label),id='store-'+field.name;label.htmlFor=id;let input;if(field.options){input=node('select');for(const choice of field.options){const option=node('option',choice);option.value=choice;input.append(option);}}else input=node(field.long?'textarea':'input');input.id=id;input.name=field.name;input.value=field.value??'';input.disabled=Boolean(field.disabled);if(field.maxLength)input.maxLength=field.maxLength;if(field.number&&input.tagName==='INPUT'){input.type='number';input.step='any';}if(field.file){input.type='file';input.accept='image/png,image/jpeg,image/gif,image/webp';}if(field.placeholder)input.placeholder=field.placeholder;row.append(label,input);$('store-fields').append(row);inputs[field.name]=input;}editorSave=()=>save(inputs);$('store-editor').showModal();return inputs;}

function openDatabaseEditor(row){if(!databaseData)return;const table=databaseData.table,group=selected;const defaults={kind:'fact',keywords:'[]',evidence:'[]',count:1,learned:0,stamp:Math.floor(Date.now()/1000),confidence:.8,uses:0,positive:0,negative:0,last_used:0};const fields=databaseData.columns.map(column=>({name:column.name,label:columnTitles[column.name]||column.name,value:row?row[column.name]:defaults[column.name]??'',disabled:Boolean(row&&column.pk),long:['content','text','keywords','evidence'].includes(column.name),number:/INT|REAL/.test(column.type),options:column.name==='kind'?['fact','preference','event','episode','jargon','expression']:null}));openEditor(row?'编辑记录 #'+row._rowid:'新增记录',fields,async inputs=>{const values={};for(const field of fields){if(field.disabled)continue;const raw=inputs[field.name].value;if(!row&&!raw&&field.name==='id')continue;const value=field.number&&raw!==''?Number(raw):raw;if(row&&value===row[field.name])continue;values[field.name]=value;}await act({action:'database',group,table,mode:row?'update':'create',row_id:row?._rowid,expected:row||null,values},$('store-save'));await loadDatabase();});}
$('store-close').onclick=()=>{$('store-editor').close();editorSave=null;};$('store-form').onsubmit=async event=>{event.preventDefault();if(editorBusy||!editorSave)return;const save=editorSave;try{await save();$('store-editor').close();editorSave=null;}catch(error){toast(error.message,true);}};
$('db-table').onchange=()=>{dbOffset=0;loadDatabase().catch(error=>toast(error.message,true));};$('db-refresh').onclick=()=>loadDatabase().catch(error=>toast(error.message,true));$('db-search').oninput=()=>{dbOffset=0;loadDatabase().catch(error=>toast(error.message,true));};$('db-add').onclick=()=>openDatabaseEditor(null);$('db-prev').onclick=()=>{dbOffset=Math.max(0,dbOffset-50);loadDatabase();};$('db-next').onclick=()=>{dbOffset+=50;loadDatabase();};

const stickerStates={approved:'已入库',pending:'待审核',rejected:'未通过',unreviewed:'未审核'};
async function loadStickers(){const ticket=++stickerSequence;const data=await api('/api/stickers?'+new URLSearchParams({q:$('sticker-search').value,offset:stickerOffset}));if(ticket!==stickerSequence)return;if(data.offset>0&&data.offset>=data.total){stickerOffset=Math.max(0,Math.floor((data.total-1)/data.limit))*data.limit;return loadStickers();}stickerData=data;updatePager('sticker',data);for(const url of imageURLs)URL.revokeObjectURL(url);imageURLs.length=0;const area=$('sticker-records');area.replaceChildren();for(const row of data.rows){const card=node('article',undefined,'sticker-entry'),preview=node('div',undefined,'sticker-preview');if((row.kind==='sticker'&&row.state==='approved')||row.state==='pending'){const img=node('img');img.alt=row.title;img.loading='lazy';preview.append(img);fetch('/api/sticker-image?'+new URLSearchParams({id:row.id}),{headers:{'X-QQ-Debug-Key':token}}).then(async response=>{if(!response.ok)throw Error('图片不可用');const blob=await response.blob();if(ticket!==stickerSequence)return;const url=URL.createObjectURL(blob);imageURLs.push(url);img.src=url;}).catch(()=>{preview.replaceChildren(node('span','图片文件丢失','hint'));});}else preview.append(node('div',row.kind==='market_face'?row.title:row.state==='rejected'?'审核未通过，原图没有保存':'需要重新审核','market-name'));const meta=node('div',undefined,'sticker-meta');meta.append(node('span',stickerStates[row.state]||row.state,'sticker-state '+row.state),document.createTextNode((row.kind==='market_face'?'QQ 商城表情':row.emotion||row.asset_type||'图片')+' · 用过 '+(row.uses||0)+' 次'));card.append(preview,node('div',row.title,'sticker-title'),meta);if(row.use_when)card.append(node('div','适合：'+row.use_when,'sticker-meta'));const code=node('div',row.id,'key');code.title=row.content_hash||row.id;card.append(code);const tools=node('div',undefined,'sticker-tools');if((row.kind==='sticker'&&row.state==='approved')||row.state==='pending'){const view=node('button','查看原图');view.type='button';view.onclick=async()=>{try{const url=await imageURL('/api/sticker-image?'+new URLSearchParams({id:row.id}));imageURLs.push(url);showOriginal(url,row.title);}catch(error){toast(error.message,true);}};tools.append(view);}if(['sticker','market_face'].includes(row.kind)){const edit=node('button','编辑');edit.type='button';edit.onclick=()=>openStickerEditor(row);tools.append(edit);}if(row.state!=='rejected'){const remove=node('button',row.state==='pending'?'取消收录':'删除','danger-text');remove.type='button';remove.onclick=async()=>{if(!confirm('从共享表情库删除这一项？所有授权群都会同步生效。'))return;try{await act({action:'sticker',mode:'delete',id:row.id},remove);await loadStickers();}catch{}};tools.append(remove);}card.append(tools);area.append(card);}if(!data.rows.length)area.append(node('div',$('sticker-search').value?'没有匹配的表情':'表情库还是空的。Bot 会自动收录群里反复出现的表情，也可以点右上角上传','empty'));$('sticker-count').textContent='共 '+data.total+' 项 · 第 '+(Math.floor(data.offset/24)+1)+' / '+Math.max(1,Math.ceil(data.total/24))+' 页';$('sticker-prev').disabled=!data.offset;$('sticker-next').disabled=data.offset+data.rows.length>=data.total;}

function openStickerEditor(row){const fields=row.kind==='market_face'?[{name:'emotion',label:'情绪分类',value:row.emotion||'其他',options:stickerData.emotions},{name:'use_when',label:'适用场景',value:row.use_when},{name:'package_name',label:'QQ 表情包名称',value:row.package_name,placeholder:'QQ 没有提供名称，可以手动补写'},{name:'emoji_name',label:'单个表情名称',value:row.emoji_name},{name:'label',label:'显示说明',value:row.label}]:[{name:'label',label:'显示名称',value:row.label},{name:'description',label:'图片说明',value:row.description},{name:'emotion',label:'情绪分类',value:row.emotion||'其他',options:stickerData.emotions},{name:'use_when',label:'适用场景',value:row.use_when}];openEditor('编辑表情',fields,async inputs=>{const values=Object.fromEntries(fields.map(field=>[field.name,inputs[field.name].value]));await act({action:'sticker',mode:'update',id:row.id,values},$('store-save'));await loadStickers();},'只修改名称和用途，审核结果和 QQ 原始发送信息保持不变');}
$('sticker-add').onclick=()=>{if(!snapshot?.features?.includes('image_management'))return toast('请 /exit 或 /stop 后重新启动 Debug 加载人工收录',true);const group=selected;const fields=[{name:'file',label:'图片文件（≤2MB）',file:true},...stickerFields(),{name:'admission',label:'收录方式',value:'人工确认直接入库',options:['人工确认直接入库','交给自动审核']}];const inputs=openEditor('手动录入表情包',fields,async inputs=>{const file=inputs.file.files[0];if(!file)throw Error('请选择图片');if(file.size>2*1024*1024)throw Error('图片上限2MB');const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const values=Object.fromEntries(['label','description','emotion','use_when'].map(key=>[key,inputs[key].value]));values.image_base64=encoded;values.admission=inputs.admission.value==='人工确认直接入库'?'manual':'review';await act({action:'sticker',mode:'create',values},$('store-save'));await loadStickers();},'人工确认会直接加入共享表情库并保留原图；选择自动审核时，通过后才可发送。');enablePrefill(inputs,{group,token:()=>inputs.file.files[0],prepare:async()=>({source:'upload',image_base64:await encodeStickerFile(inputs.file.files[0])})});};
function updatePager(prefix,data){const input=$(prefix+'-page'),pages=Math.max(1,Math.ceil(data.total/data.limit));input.max=String(pages);if(document.activeElement!==input)input.value=String(Math.floor(data.offset/data.limit)+1);input.disabled=data.total===0;$(prefix+'-jump').disabled=data.total===0;}
function jumpPage(prefix){const data=prefix==='db'?databaseData:stickerData;if(!data)return;const input=$(prefix+'-page'),page=Number(input.value),pages=Math.max(1,Math.ceil(data.total/data.limit));if(!Number.isInteger(page)||page<1||page>pages){toast('页码请输入1～'+pages+'的整数',true);input.focus();return;}const offset=(page-1)*data.limit;if(offset===data.offset)return;if(prefix==='db'){dbOffset=offset;loadDatabase().catch(error=>toast(error.message,true));}else{stickerOffset=offset;loadStickers().catch(error=>toast(error.message,true));}}
for(const prefix of ['db','sticker']){$(prefix+'-jump').onclick=()=>jumpPage(prefix);$(prefix+'-page').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();jumpPage(prefix);}};}
$('sticker-refresh').onclick=()=>loadStickers().catch(error=>toast(error.message,true));$('sticker-search').oninput=()=>{stickerOffset=0;loadStickers().catch(error=>toast(error.message,true));};$('sticker-prev').onclick=()=>{stickerOffset=Math.max(0,stickerOffset-24);loadStickers();};$('sticker-next').onclick=()=>{stickerOffset+=24;loadStickers();};
setInterval(()=>{if(closed||document.hidden||document.querySelector('dialog[open]'))return;if(currentTab==='system')loadSystem().catch(()=>{});else if(currentTab==='stickers')loadStickers().catch(()=>{});},10000);

function showBackendVersion(ready){let banner=$('backend-version');if(!banner){banner=node('div',undefined,'backend-version');banner.id='backend-version';banner.setAttribute('role','status');document.body.append(banner);}banner.hidden=ready;if(!ready)banner.textContent='网页文件已经更新，但调试服务还是旧进程：请在 CLI 输入 /exit，然后重新打开 Start-QQ-Group-Bot-Debug。';document.querySelectorAll('.side-nav [data-view],[data-context-tabs] button').forEach(button=>button.disabled=!ready&&(button.dataset.view||button.dataset.tab)!=='logs');}

window.addEventListener('pagehide',()=>{for(const url of originalURLs.values())URL.revokeObjectURL(url);originalURLs.clear();});
