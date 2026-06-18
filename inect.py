import asyncio
from database import AsyncSessionLocal, Job, JobStatus, init_db

async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        test_job = Job(
            business_name="Novak Instalateri",
            phone_number="+407xxxxxxxx",  # <--- Pune numărul tău aici
            niche="stavebnictvi",
            language="Czech",
            status=JobStatus.SCRAPED  # Îl punem ca SCRAPED ca să treacă prin ambele bucle
        )
        session.add(test_job)
        await session.commit()
    print("✓ Lead de test adăugat cu succes ca SCRAPED!")

if __name__ == "__main__":
    asyncio.run(main())