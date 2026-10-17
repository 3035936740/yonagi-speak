"""Explicit connection checks without restarting QQ or unrelated MCP hosts."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from dotenv import dotenv_values
from console_config import Config,atomic,runtime
from runtime_paths import project_path


async def probe_mcp(root,group):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    root=Path(root);cfg=Config(root,group).load();package=root.parent/'qq-mcp'
    module=package/'src/napcat_mcp/server.py'
    if not module.is_file():raise ValueError('找不到qq-mcp/src/napcat_mcp/server.py，请安装NapCat MCP')
    env={**os.environ,**{key:value for key,value in dotenv_values(project_path(cfg['mcp_env_file'],root)).items() if value is not None},
        'PYTHONPATH':str(package/'src'),'PYTHONIOENCODING':'utf-8','MCP_TRANSPORT':'stdio'}
    # Match the bot's trust_env=False: the configured OneBot endpoint is direct.
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env[key]=''
    env['NO_PROXY']='*';env['no_proxy']='*'
    executable=package/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    params=StdioServerParameters(command=str(executable) if executable.is_file() else sys.executable,
        args=['-m','napcat_mcp','--transport','stdio'],cwd=str(package),env=env)
    async def check():
        with open(os.devnull,'w') as errors:
            async with stdio_client(params,errlog=errors) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    result=await session.call_tool('get_group_info',{'group_id':group})
                    if result.isError:raise ValueError('MCP群信息检查失败')
                    for part in result.content:
                        try:data=json.loads(getattr(part,'text',''))
                        except ValueError:continue
                        if isinstance(data,dict) and str(data.get('group_id'))==str(group):return True
                    from debug_events import safe_log
                    detail=' '.join(getattr(part,'text','') for part in result.content)
                    raise ValueError('MCP未返回当前群信息：'+safe_log(detail)[:180])
    return await asyncio.wait_for(check(),20)


def reconnect(ui,mode='napcat'):
    if mode not in {'napcat','mcp'}:raise ValueError('/reconnect napcat|mcp')
    cfg=Config(ui.root,ui.group).load();folder=runtime(ui.root,ui.group)
    if mode=='napcat':
        if not cfg['enabled']:raise ValueError('该群未启用，请先在行为设置启用')
        ui.supervisor.stop_group(ui.group);ui.supervisor.paused.discard(ui.group);ui.supervisor.retry_after.pop(ui.group,None);ui.supervisor.sync()
        ui.notice='当前群NapCat连接已重新启动，等待QQ登录和群信息验证；其他群不受影响'
    else:
        try:
            asyncio.run(probe_mcp(ui.root,ui.group))
        except Exception as error:
            leaf=error
            while getattr(leaf,'exceptions',None):leaf=leaf.exceptions[0]
            reason=str(leaf) if isinstance(leaf,ValueError) else type(leaf).__name__
            atomic(folder/'mcp-connection.json',{'ok':False,'checked':time.time(),'error_type':type(leaf).__name__})
            raise ValueError('NapCat MCP重新连接检查失败：'+reason+'；请检查安装、登录及群权限') from error
        atomic(folder/'mcp-connection.json',{'ok':True,'checked':time.time()})
        ui.notice='已建立新的NapCat MCP会话并验证当前群；QQ未重启。其他应用的MCP会话由其宿主管理'
