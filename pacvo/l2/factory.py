"""Token Factory for Deterministic L2 Token Deployment (CREATE & CREATE2)."""

from pacvo.crypto import derive_create_address, derive_create2_address
from pacvo.l2.token import ERC20Token, TokenType


class TokenFactory:
    """Factory utility for creating, deriving, and managing L2 token deployments."""

    @staticmethod
    def create_fixed_supply_token(
        name: str,
        symbol: str,
        initial_supply: int,
        decimals: int = 18,
    ) -> bytes:
        """Generate initcode for an immutable fixed-supply ERC-20 token."""
        return ERC20Token.build_initcode(
            name=name,
            symbol=symbol,
            decimals=decimals,
            initial_supply=initial_supply,
            token_type=TokenType.FIXED_SUPPLY,
        )

    @staticmethod
    def create_controlled_mint_token(
        name: str,
        symbol: str,
        minter: str,
        initial_supply: int = 0,
        decimals: int = 18,
    ) -> bytes:
        """Generate initcode for an authorized-mint ERC-20 token."""
        return ERC20Token.build_initcode(
            name=name,
            symbol=symbol,
            decimals=decimals,
            initial_supply=initial_supply,
            token_type=TokenType.CONTROLLED_MINT,
            minter=minter,
        )

    @staticmethod
    def create_memecoin(
        name: str,
        symbol: str,
        total_supply: int,
        decimals: int = 18,
    ) -> bytes:
        """Generate initcode for a community/memecoin asset with fixed supply."""
        return ERC20Token.build_initcode(
            name=name,
            symbol=symbol,
            decimals=decimals,
            initial_supply=total_supply,
            token_type=TokenType.FIXED_SUPPLY,
        )

    @staticmethod
    def create_nft_collection(
        name: str,
        symbol: str,
        minter: str,
    ) -> bytes:
        """Generate initcode for a standard ERC-721 Non-Fungible Token collection."""
        from pacvo.l2.nft import ERC721Token
        return ERC721Token.build_initcode(
            name=name,
            symbol=symbol,
            minter=minter,
        )

    @staticmethod
    def compute_address(deployer: str, nonce: int) -> str:
        """Deterministic address derivation via CREATE (Keccak-256(RLP([deployer, nonce])))."""
        return derive_create_address(deployer, nonce)

    @staticmethod
    def compute_address_create2(deployer: str, salt: bytes, initcode: bytes) -> str:
        """Deterministic address derivation via CREATE2 (Keccak-256(0xff || deployer || salt || Keccak-256(initcode)))."""
        return derive_create2_address(deployer, salt, initcode)
