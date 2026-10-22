import assert from "node:assert/strict";
import { mkdtemp, readdir, rm, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const props = node => node?.[Object.keys(node).find(key => key.startsWith("__reactProps"))];
const selection = {connection_id:"connection-responsive",model_id:"registered-model",reasoning_effort:null};
const fixture = () => ({
  notebookContext:{notebook_id:"notebook-responsive",sources:[]},
  async listSources(){return [{source_id:"source-responsive",source_version_id:"version-responsive",filename:"보존 자료.txt",source_state:"ready",processing_state:"completed",job_state:"completed"}];},
  async listStudioOutputs(){return [];}, async listConnectors(){return [];},
  async getModelSettings(){return {notebook_id:"notebook-responsive",version:1,selection,effective_selection:{...selection,origin:"notebook"},available_models:[{connection_id:selection.connection_id,model_id:selection.model_id,provider_code:"CUSTOM",display_name:"등록 연결",connection_version:1,catalog_version:1,reasoning:{status:"unknown",values:[],profile_version:null}}]};},
  uploadPdf(){throw Error("UNEXPECTED_MUTATION");},getProcessingStatus(){throw Error("UNEXPECTED_MUTATION");},
  askQuestion(){throw Error("UNEXPECTED_MODEL_CALL");},createReport(){throw Error("UNEXPECTED_MUTATION");},citationUrl(){return "/bff/api/unused";},
});

async function mount(width, overrides = {}) {
  const root=path.resolve(import.meta.dirname,"../.."), parent=path.resolve(tmpdir());
  assert.equal(await realpath(parent),parent);
  const output=await mkdtemp(path.join(parent,"r4-responsive-")),dom=installMinimalDom();
  let reactRoot, currentWidth=width; const listeners=new Set();
  // 이 대역은 focus용 selector만 제공한다. 실제 CSS·inert 동작은 별도 브라우저로 검증한다.
  const createElementNode=dom.document.createElement;
  dom.document.createElement=tag=>{
    const node=createElementNode(tag);
    node.querySelectorAll=()=>findElements(node,item=>["BUTTON","INPUT","SELECT","TEXTAREA"].includes(item.tagName)&&!item.disabled&&!item.hasAttribute("hidden"));
    node.querySelector=()=>node.querySelectorAll()[0];
    return node;
  };
  window.innerWidth=width;
  const media={get matches(){return currentWidth>=1024&&currentWidth<=1439;},addEventListener(_type,callback){listeners.add(callback);},removeEventListener(_type,callback){listeners.delete(callback);}};
  window.matchMedia=()=>media;
  try {
    const {build}=await import("vite"),{createElement,act}=await import("react"),{createRoot}=await import("react-dom/client");
    await build({configFile:false,logLevel:"silent",root,build:{outDir:output,emptyOutDir:false,lib:{entry:path.join(root,"packages/ui/src/product-workspace-shell.jsx"),formats:["es"],fileName:"responsive"},rollupOptions:{external:["react","react-dom","react-dom/client"],output:{paths:Object.fromEntries(["react","react-dom","react-dom/client"].map(id=>[id,import.meta.resolve(id)]))}}}});
    const entry=(await readdir(output)).find(name=>/\.m?js$/u.test(name));
    const {ProductWorkspaceShell}=await import(pathToFileURL(path.join(output,entry)).href);
    const container=dom.document.createElement("div");dom.document.body.appendChild(container);reactRoot=createRoot(container);
    await act(async()=>{reactRoot.render(createElement(ProductWorkspaceShell,{workspaceId:"workspace-responsive",adapter:{...fixture(),...overrides}}));await Promise.resolve();await Promise.resolve();});
    const pane=id=>findElements(container,node=>node.getAttribute("id")===`product-pane-${id}`)[0];
    for(const id of ["sources","conversation","studio"]){
      const section=pane(id);
      section.querySelectorAll=()=>findElements(section,node=>["BUTTON","INPUT","SELECT","TEXTAREA"].includes(node.tagName)&&!node.disabled&&!node.hasAttribute("hidden"));
      section.querySelector=()=>section.querySelectorAll()[0];
    }
    return {container,dom,act,pane,
      button:text=>findElements(container,node=>node.tagName==="BUTTON"&&node.textContent.trim()===text)[0],
      click:node=>act(async()=>{assert.ok(node,"클릭 대상");node.dispatchEvent(new MinimalEvent("click"));await Promise.resolve();}),
      resize:next=>act(async()=>{currentWidth=next;window.innerWidth=next;for(const callback of listeners)callback({matches:media.matches});await Promise.resolve();}),
      async cleanup(){await act(async()=>reactRoot.unmount());dom.restore();assert.equal(path.dirname(output),parent);assert.equal(await realpath(output),output);await rm(output,{recursive:true,force:true});},
    };
  }catch(error){if(reactRoot){const {act}=await import("react");await act(async()=>reactRoot.unmount());}dom.restore();await rm(output,{recursive:true,force:true});throw error;}
}

test("R4 1200폭은 실제 두 pane과 동일 DOM Drawer를 제공하고 초안·scroll·모델을 보존한다",async()=>{
  const view=await mount(1200);
  try{
    const layout=findElements(view.container,node=>node.getAttribute("data-layout")==="source-conversation-studio")[0];
    assert.equal(layout.getAttribute("data-responsive-layout"),"two-pane");
    const source=view.pane("sources"),conversation=view.pane("conversation"),studio=view.pane("studio");
    assert.equal(studio.parentNode.getAttribute("data-pane-placement"),"drawer");
    assert.equal(studio.parentNode.hasAttribute("hidden"),true);
    const question=findElements(conversation,node=>node.tagName==="TEXTAREA")[0];
    await view.act(async()=>props(question).onChange({currentTarget:{value:"보존할 질문 초안"}}));
    const transcript=findElements(conversation,node=>node.getAttribute("aria-label")==="질문과 답변 이력")[0];transcript.scrollTop=87;
    const model=findElements(conversation,node=>node.getAttribute("aria-label")==="Provider · 모델")[0];
    await view.click(findElements(source,node=>node.tagName==="BUTTON"&&node.textContent.includes("Source 추가"))[0]);
    await view.click(view.button("복사한 텍스트"));
    const paste=findElements(source,node=>node.tagName==="TEXTAREA"&&node.getAttribute("placeholder")==="복사한 텍스트를 붙여 넣으세요")[0];
    await view.act(async()=>props(paste).onChange({target:{value:"보존할 자료 초안"}}));
    await view.click(view.button("대화·Studio"));
    assert.equal(source.parentNode.getAttribute("data-pane-placement"),"drawer");
    assert.equal(conversation.parentNode.getAttribute("data-pane-placement"),"inline");
    await view.click(findElements(studio,node=>node.tagName==="BUTTON"&&node.textContent.includes("근거 기반 보고서"))[0]);
    const purpose=findElements(studio,node=>node.tagName==="INPUT"&&node.getAttribute("placeholder")==="이 산출물로 해결할 업무")[0];
    assert.ok(purpose);
    await view.act(async()=>props(purpose).onChange({currentTarget:{value:"보존할 Studio 목적"}}));
    const opener=view.button("자료 열기");opener.focus();await view.click(opener);
    assert.equal(source.getAttribute("role"),"dialog");assert.equal(source.getAttribute("aria-modal"),"true");
    assert.equal(conversation.parentNode.hasAttribute("inert"),true);
    const close=findElements(source,node=>node.getAttribute("aria-label")==="보조 패널 닫기")[0];
    assert.equal(view.dom.document.activeElement,close);
    const focusable=source.querySelectorAll();focusable.at(-1).focus();let prevented=false;
    await view.act(async()=>props(source).onKeyDown({key:"Tab",currentTarget:source,preventDefault(){prevented=true;}}));
    assert.equal(prevented,true);assert.equal(view.dom.document.activeElement,close);
    await view.act(async()=>props(source).onKeyDown({key:"Escape",currentTarget:source,preventDefault(){}}));
    assert.equal(source.parentNode.hasAttribute("hidden"),true);assert.equal(view.dom.document.activeElement,opener);
    await view.resize(1920);
    assert.equal(layout.getAttribute("data-responsive-layout"),"three-pane");
    assert.equal(source.parentNode.hasAttribute("hidden"),false);
    assert.equal(view.pane("sources"),source);assert.equal(view.pane("conversation"),conversation);assert.equal(view.pane("studio"),studio);
    assert.equal(question.value,"보존할 질문 초안");assert.equal(paste.value,"보존할 자료 초안");assert.equal(transcript.scrollTop,87);assert.equal(props(model).value,JSON.stringify([selection.connection_id,selection.model_id]));
    assert.equal(purpose.value,"보존할 Studio 목적");
    assert.equal(findElements(studio,node=>node.getAttribute("placeholder")==="이 산출물로 해결할 업무")[0],purpose);
    await view.resize(1200);assert.equal(source.parentNode.getAttribute("data-pane-placement"),"drawer");
    assert.equal(question.value,"보존할 질문 초안");assert.equal(paste.value,"보존할 자료 초안");
  }finally{await view.cleanup();}
});


test("R4 Drawer의 질문 추가인증 modal은 단일 초점 경계를 유지하고 resize·취소 후 입력을 보존한다",async()=>{
  let calls=0,authorizations=0;
  const view=await mount(1200,{askQuestion:async()=>{calls++;throw Error("STEP_UP_REQUIRED");},authorizeQuestion:async()=>{authorizations++;throw Error("UNEXPECTED_AUTHORIZATION");}});
  try{
    await view.click(view.button("자료·Studio"));await view.click(view.button("대화 열기"));
    const conversation=view.pane("conversation"),question=findElements(conversation,node=>node.tagName==="TEXTAREA")[0];
    await view.act(async()=>props(question).onChange({currentTarget:{value:"안녕하세요"}}));
    const submit=findElements(conversation,node=>node.getAttribute("aria-label")==="질문 실행")[0];submit.focus();
    const form=findElements(conversation,node=>node.tagName==="FORM"&&node.getAttribute("class")==="conversation-composer")[0];
    await view.act(async()=>{void props(form).onSubmit({preventDefault(){}});await Promise.resolve();await Promise.resolve();});
    const modal=findElements(view.container,node=>node.getAttribute("aria-labelledby")==="workspace-modal-title")[0];
    assert.ok(modal);assert.equal(modal.getAttribute("aria-modal"),"true");
    assert.equal(conversation.getAttribute("aria-modal"),null);assert.equal(props(conversation).onKeyDown,undefined);
    const surface=findElements(view.container,node=>node.getAttribute("class")==="workspace-surface")[0];
    assert.equal(surface.hasAttribute("inert"),true);
    await view.resize(1920);
    await view.act(async()=>props(modal).onKeyDown({key:"Escape",currentTarget:modal,preventDefault(){}}));
    assert.equal(findElements(view.container,node=>node.getAttribute("aria-labelledby")==="workspace-modal-title").length,0);
    assert.equal(surface.hasAttribute("inert"),false);assert.equal(view.dom.document.activeElement,submit);
    assert.equal(question.value,"안녕하세요");assert.equal(calls,1);assert.equal(authorizations,0);
    assert.equal(view.pane("conversation"),conversation);
  }finally{await view.cleanup();}
});



test("R4 넓은 화면의 pane 초점으로 자동 열린 Drawer도 현재 보이는 opener로 복귀한다",async()=>{
  const view=await mount(1920);
  try{
    const studio=view.pane("studio");
    const studioControl=findElements(studio,node=>node.tagName==="BUTTON"&&!node.hasAttribute("hidden"))[0];
    studioControl.focus();
    await view.resize(1200);
    assert.equal(studio.getAttribute("role"),"dialog");
    await view.act(async()=>props(studio).onKeyDown({key:"Escape",currentTarget:studio,preventDefault(){}}));
    assert.equal(studio.parentNode.hasAttribute("hidden"),true);
    const firstOpener=view.button("Studio 열기");
    assert.equal(view.dom.document.activeElement===firstOpener,true);
    assert.equal(firstOpener.parentNode.hasAttribute("hidden"),false);
    await view.click(view.button("자료·Studio"));
    await view.resize(1920);
    const conversation=view.pane("conversation"),question=findElements(conversation,node=>node.tagName==="TEXTAREA")[0];
    question.focus();
    await view.resize(1200);
    assert.equal(conversation.getAttribute("role"),"dialog");
    await view.click(findElements(conversation,node=>node.getAttribute("aria-label")==="보조 패널 닫기")[0]);
    const currentOpener=view.button("대화 열기");
    assert.equal(view.dom.document.activeElement===currentOpener,true);
    assert.equal(currentOpener.getAttribute("aria-controls"),"product-pane-conversation");
    assert.equal(conversation.parentNode.hasAttribute("hidden"),true);
  }finally{await view.cleanup();}
});
