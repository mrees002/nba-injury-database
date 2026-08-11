from sqlalchemy import BigInteger, Integer

BIGINT = BigInteger().with_variant(Integer, "sqlite")
