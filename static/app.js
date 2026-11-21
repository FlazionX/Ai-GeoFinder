const $ = (id) => document.getElementById(id);
const input = $("fileInput"), zone = $("dropzone"), preview = $("preview");
let selectedFile = null, objectUrl = null;
function toast(message){const el=$("toast");el.textContent=message;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2600)}
function setFile(file){
  if(!file)return;
  if(!["image/jpeg","image/png","image/webp"].includes(file.type)){toast("Выбери JPG, PNG или WEBP");return}
  if(file.size>12*1024*1024){toast("Файл больше 12 МБ");return}
  selectedFile=file;
  if(objectUrl)URL.revokeObjectURL(objectUrl);
  objectUrl=URL.createObjectURL(file);preview.src=objectUrl;preview.hidden=false;
  $("uploadContent").hidden=true;$("removeImage").hidden=false;$("analyzeBtn").disabled=false;
  $("fileMeta").textContent=file.name+" · "+(file.size/1024/1024).toFixed(2)+" МБ";
  $("results").hidden=true;$("emptyState").hidden=false;$("errorBox").hidden=true;
}
input.addEventListener("change",()=>setFile(input.files[0]));
["dragenter","dragover"].forEach(evt=>zone.addEventListener(evt,e=>{e.preventDefault();zone.classList.add("dragover")}));
["dragleave","drop"].forEach(evt=>zone.addEventListener(evt,e=>{e.preventDefault();zone.classList.remove("dragover")}));
zone.addEventListener("drop",e=>setFile(e.dataTransfer.files[0]));
$("removeImage").addEventListener("click",e=>{e.preventDefault();e.stopPropagation();clearFile()});
function clearFile(){selectedFile=null;input.value="";preview.hidden=true;preview.removeAttribute("src");$("uploadContent").hidden=false;$("removeImage").hidden=true;$("analyzeBtn").disabled=true;$("fileMeta").textContent="Файл не выбран";if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null}
function safeText(value){return typeof value==="string"?value:""}
function renderList(id,items){const root=$(id);root.replaceChildren();(Array.isArray(items)?items:[]).forEach(item=>{const li=document.createElement("li");li.textContent=typeof item==="string"?item:JSON.stringify(item);root.append(li)})}
function renderCandidates(items){
 const root=$("candidates");root.replaceChildren();
 (Array.isArray(items)?items:[]).forEach(item=>{
  const card=document.createElement("div");card.className="candidate";
  const main=document.createElement("div");main.className="candidate-main";
  const name=document.createElement("div");name.className="candidate-name";name.textContent=safeText(item.country)||"Регион не определён";
  const region=document.createElement("div");region.className="candidate-region";region.textContent=safeText(item.region);
  const bar=document.createElement("div");bar.className="confidence-bar";const fill=document.createElement("span");
  const score=Math.max(0,Math.min(100,Number(item.confidence)||0));fill.style.width=score+"%";bar.append(fill);
  main.append(name,region,bar);const confidence=document.createElement("div");confidence.className="confidence";confidence.textContent=score+"%";
  card.append(main,confidence);root.append(card);
 });
 if(!root.children.length){const p=document.createElement("p");p.className="candidate-region";p.textContent="Надёжных кандидатов не найдено.";root.append(p)}
}
$("analyzeBtn").addEventListener("click",async()=>{
 if(!selectedFile)return;
 $("emptyState").hidden=true;$("results").hidden=true;$("errorBox").hidden=true;$("loadingState").hidden=false;
 $("analyzeBtn").disabled=true;
 const body=new FormData();body.append("image",selectedFile);
 try{
  const response=await fetch("/api/analyze",{method:"POST",body});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||"Не удалось выполнить анализ");
  const result=data.result||{};
  $("summary").textContent=safeText(result.summary)||"Анализ завершён.";
  renderCandidates(result.country_candidates);renderList("clues",result.visual_clues);renderList("limitations",result.limitations);
  $("notice").textContent=data.notice||"Результат является предположением.";
  $("results").hidden=false;
 }catch(error){$("errorBox").textContent=error.message||"Неизвестная ошибка";$("errorBox").hidden=false}
 finally{$("loadingState").hidden=true;$("analyzeBtn").disabled=!selectedFile}
});
fetch("/api/health").then(r=>r.json()).then(data=>{$("apiStatus").textContent=data.ai_configured?"ИИ готов к работе":"Нужен API-ключ";$("apiStatus").previousElementSibling.style.background=data.ai_configured?"#c7f36b":"#e6a64d"}).catch(()=>{$("apiStatus").textContent="Сервер активен"});
$("settingsBtn").addEventListener("click",()=>toast("Для подключения ИИ добавь GEMINI_API_KEY в файл .env"));
