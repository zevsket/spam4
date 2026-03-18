from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON
from datetime import datetime
import json

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Account(Base):
    __tablename__ = 'accounts'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    phone = Column(String, unique=True)
    session_string = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class SpamTask(Base):
    __tablename__ = 'spam_tasks'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    account_id = Column(Integer)
    links = Column(Text)  # JSON строка со ссылками
    message = Column(Text)
    delay_min = Column(Integer, default=5)
    delay_max = Column(Integer, default=15)
    is_running = Column(Boolean, default=False)
    total_sent = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

async def init_db():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        return session