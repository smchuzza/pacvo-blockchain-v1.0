// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title PacvoNFT (ERC-721 Standard Non-Fungible Token)
 * @notice Production-grade ERC-721 implementation for Pacvo Layer 2.
 * @dev Complies with ERC-721, ERC-721Metadata, and ERC-721Enumerable core interfaces.
 */

interface IERC721 {
    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Approval(address indexed owner, address indexed approved, uint256 indexed tokenId);
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);

    function balanceOf(address owner) external view returns (uint256 balance);
    function ownerOf(uint256 tokenId) external view returns (address owner);
    function safeTransferFrom(address from, address to, uint256 tokenId) external;
    function transferFrom(address from, address to, uint256 tokenId) external;
    function approve(address to, uint256 tokenId) external;
    function getApproved(uint256 tokenId) external view returns (address operator);
    function setApprovalForAll(address operator, bool approved) external;
    function isApprovedForAll(address owner, address operator) external view returns (bool);
}

interface IERC721Metadata {
    function name() external view returns (string memory);
    function symbol() external view returns (string memory);
    function tokenURI(uint256 tokenId) external view returns (string memory);
}

contract PacvoNFT is IERC721, IERC721Metadata {
    string private _name;
    string private _symbol;
    address public minter;
    uint256 public totalSupply;

    // Mapping from token ID to owner address
    mapping(uint256 => address) private _owners;

    // Mapping owner address to token count
    mapping(address => uint256) private _balances;

    // Mapping from token ID to approved address
    mapping(uint256 => address) private _tokenApprovals;

    // Mapping from owner to operator approvals
    mapping(address => mapping(address => bool)) private _operatorApprovals;

    // Mapping from token ID to token URI
    mapping(uint256 => string) private _tokenURIs;

    modifier onlyMinter() {
        require(msg.sender == minter, "PacvoNFT: caller is not the minter");
        _;
    }

    constructor(string memory name_, string memory symbol_, address minter_) {
        _name = name_;
        _symbol = symbol_;
        minter = minter_ == address(0) ? msg.sender : minter_;
    }

    function name() public view override returns (string memory) {
        return _name;
    }

    function symbol() public view override returns (string memory) {
        return _symbol;
    }

    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(_owners[tokenId] != address(0), "PacvoNFT: URI query for nonexistent token");
        return _tokenURIs[tokenId];
    }

    function setTokenURI(uint256 tokenId, string memory uri) public onlyMinter {
        require(_owners[tokenId] != address(0), "PacvoNFT: URI set for nonexistent token");
        _tokenURIs[tokenId] = uri;
    }

    function balanceOf(address owner) public view override returns (uint256) {
        require(owner != address(0), "PacvoNFT: address zero is not a valid owner");
        return _balances[owner];
    }

    function ownerOf(uint256 tokenId) public view override returns (address) {
        address owner = _owners[tokenId];
        require(owner != address(0), "PacvoNFT: invalid token ID");
        return owner;
    }

    function approve(address to, uint256 tokenId) public override {
        address owner = ownerOf(tokenId);
        require(to != owner, "PacvoNFT: approval to current owner");
        require(msg.sender == owner || isApprovedForAll(owner, msg.sender), "PacvoNFT: not authorized");
        _tokenApprovals[tokenId] = to;
        emit Approval(owner, to, tokenId);
    }

    function getApproved(uint256 tokenId) public view override returns (address) {
        require(_owners[tokenId] != address(0), "PacvoNFT: approved query for nonexistent token");
        return _tokenApprovals[tokenId];
    }

    function setApprovalForAll(address operator, bool approved) public override {
        require(operator != msg.sender, "PacvoNFT: approve to caller");
        _operatorApprovals[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function isApprovedForAll(address owner, address operator) public view override returns (bool) {
        return _operatorApprovals[owner][operator];
    }

    function transferFrom(address from, address to, uint256 tokenId) public override {
        require(_isApprovedOrOwner(msg.sender, tokenId), "PacvoNFT: caller is not token owner or approved");
        _transfer(from, to, tokenId);
    }

    function safeTransferFrom(address from, address to, uint256 tokenId) public override {
        transferFrom(from, to, tokenId);
    }

    function mint(address to, uint256 tokenId) public onlyMinter {
        require(to != address(0), "PacvoNFT: mint to zero address");
        require(_owners[tokenId] == address(0), "PacvoNFT: token already minted");

        _balances[to] += 1;
        _owners[tokenId] = to;
        totalSupply += 1;

        emit Transfer(address(0), to, tokenId);
    }

    function mintWithURI(address to, uint256 tokenId, string memory uri) public onlyMinter {
        mint(to, tokenId);
        _tokenURIs[tokenId] = uri;
    }

    function burn(uint256 tokenId) public {
        address owner = ownerOf(tokenId);
        require(msg.sender == owner || _isApprovedOrOwner(msg.sender, tokenId), "PacvoNFT: not owner or approved");

        delete _tokenApprovals[tokenId];
        _balances[owner] -= 1;
        delete _owners[tokenId];
        totalSupply -= 1;

        emit Transfer(owner, address(0), tokenId);
    }

    function _isApprovedOrOwner(address spender, uint256 tokenId) internal view returns (bool) {
        address owner = ownerOf(tokenId);
        return (spender == owner || isApprovedForAll(owner, spender) || getApproved(tokenId) == spender);
    }

    function _transfer(address from, address to, uint256 tokenId) internal {
        require(ownerOf(tokenId) == from, "PacvoNFT: transfer from incorrect owner");
        require(to != address(0), "PacvoNFT: transfer to zero address");

        delete _tokenApprovals[tokenId];
        _balances[from] -= 1;
        _balances[to] += 1;
        _owners[tokenId] = to;

        emit Transfer(from, to, tokenId);
    }
}
