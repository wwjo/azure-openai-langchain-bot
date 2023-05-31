from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain.chat_models import AzureChatOpenAI
from langchain.memory import ConversationSummaryBufferMemory, PostgresChatMessageHistory
from langchain.agents import initialize_agent, AgentType
from langchain.callbacks import tracing_enabled
from callback import CustomHandler, WSHandler
from dotenv import load_dotenv
import asyncio 
import os
import logging

#from tools.duckduckgosearchtool import duckduckgosearchtool
#from tools.pythontool import pythontool
#from tools.azurecognitivesearchtool import azurecognitivesearchtool

# IMPORT TOOL START
#from tools.bingsearchtool import bingsearchtool
#from tools.shelltool import shelltool
#from tools.docsimport import docsimport
#from tools.chatgptplugins import chatgptplugins
#from tools.zapiertool import zapiertool
#from tools.customtools import customtools
# IMPORT TOOL END

load_dotenv()

memories = {}
history = {}
agents = {}
agent_chains = {}
tools = []


azchat=AzureChatOpenAI(
    client=None,
    openai_api_base=str(os.getenv("OPENAI_API_BASE")),
    openai_api_version="2023-03-15-preview",
    deployment_name=str(os.getenv("CHAT_DEPLOYMENT_NAME")),
    openai_api_key=str(os.getenv("OPENAI_API_KEY")),
    # openai_api_type = "azure"
)
# tools = load_tools(["llm-math"], llm=azchat)

#tools.extend(duckduckgosearchtool())
#tools.extend(pythontool())
#tools.extend(azurecognitivesearchtool())

# ADD TOOL START 
#tools.extend(bingsearchtool())
#tools.extend(shelltool())
#tools.extend(docsimport(azchat))
#tools.extend(chatgptplugins())
#tools.extend(zapiertool())
#tools.extend(customtools()) 
# ADD TOOL END

tool_names = [tool.name for tool in tools]

print(tool_names) 

def SetupChatAgent(id, callbacks):
    # memories[id] = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
    postgresUser = str(os.getenv("POSTGRES_USER"))
    postgresPassword = str(os.getenv("POSTGRES_PASSWORD"))
    postgresHost = str(os.getenv("POSTGRES_HOST"))
    postgresPort = str(os.getenv("POSTGRES_PORT"))
    memories[id] = ConversationSummaryBufferMemory(
        llm=azchat, 
        max_token_limit=2500, 
        memory_key="chat_history", 
        return_messages=True) 
    memories[id].save_context(
        {"input": os.getenv("CHAT_SYSTEM_PROMPT")}, 
        {"ouputs": "I will follow the instructions."}) 
    history[id] = PostgresChatMessageHistory(
        connection_string=f"postgresql://{postgresUser}:{postgresPassword}@{postgresHost}:{postgresPort}/chat_history", 
        session_id=str(id))
    agent_chains[id] = initialize_agent(
        tools,
        azchat, 
        agent=AgentType.CHAT_CONVERSATIONAL_REACT_DESCRIPTION, 
        # agent=AgentType.CHAT_ZERO_SHOT_REACT_DESCRIPTION, 
        # agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION, 
        verbose=True, 
        memory=memories[id], 
        handle_parsing_errors=True,
        max_iterations=10, 
        early_stopping_method="generate",
        callbacks=callbacks)

class MessageReq(BaseModel):
    id: str
    text: str

class MessageRes(BaseModel):
    result: str

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def keepAsking(mid, text):
    res = ""
    try:
        res = agent_chains[mid].run(input=text)
    except:
        res = keepAsking(mid, text)
    return res

def clearMemory(mid):
    newMessage = str(os.getenv("CHAT_SYSTEM_PROMPT")) + "\nThe summary as below:\n" + agent_chains[mid].memory.predict_new_summary(agent_chains[mid].memory.buffer, agent_chains[mid].memory.moving_summary_buffer)
    agent_chains[mid].memory.buffer.clear()
    agent_chains[mid].memory.save_context({"input": newMessage}, {"ouputs": "OK, I will follow the instructions now."})

@app.post("/run")
def run(msg: MessageReq):
    if (msg.id not in agent_chains):
        SetupChatAgent(msg.id, [CustomHandler(session_id=msg.id)])
    response = agent_chains[msg.id].run(input=msg.text)
    # response = keepAsking(msg.id, msg.text)
    history[msg.id].add_user_message(msg.text)
    history[msg.id].add_ai_message(response)
    # clearMemory(msg.id)
    print("------MEMORY ID: " + msg.id + "-----")
    if (agent_chains[msg.id].memory.llm.get_num_tokens_from_messages(agent_chains[msg.id].memory.buffer) > 2000):
        clearMemory(msg.id)
    print("Conversation History: ")
    print(agent_chains[msg.id].memory.buffer)
    print("------END OF MEMORY （" + str(len(agent_chains[msg.id].memory.buffer)) + ")-----")

    result = MessageRes(result=response)
    return result

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        while True:
            try:
                msg = await websocket.receive_json()
                msg = MessageReq(**msg)

                if (msg.id not in agent_chains):
                    SetupChatAgent(msg.id, [CustomHandler(session_id=msg.id), WSHandler(websocket=websocket, session_id=msg.id)])
                    await websocket.send_json({
                        "result": "Enabled Tools: " + str(tool_names)
                    }) 

                response = await asyncio.create_task(agent_chains[msg.id].arun(msg.text))
                await websocket.send_json({
                    "result": response
                    }) 
                
            except WebSocketDisconnect:
                logging.info("websocket disconnect")
                break
            except Exception as e:
                logging.error(e)
                await websocket.send_json({
                    "error": str(e)
                })

@app.get("/tools")
def get_tools():
    tool_list = []
    for tool in tools:
        tool_dict = {"name": tool.name, "description": tool.description}
        tool_list.append(tool_dict)
    return {"tools": tool_list}

# RESTART: ca43e2c8-ea0f-463f-9df5-11ef589b7170
